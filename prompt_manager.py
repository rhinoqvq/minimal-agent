"""Markdown 模板、工具说明、消息组装与达到预算 80% 时的上下文压缩。"""

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Callable


PROMPT_DIR = Path(__file__).resolve().parent / "prompt_store"


def read_prompt(path: str | Path) -> str:
    """读取单个 Markdown prompt 文件，原样返回 UTF-8 文本。"""
    path = Path(path)
    if path.suffix != ".md":
        raise ValueError("prompt 必须是 .md 文件")
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise ValueError(f"prompt 文件为空：{path}")
    return content


class ContextBudgetError(RuntimeError):
    """模型没有生成可用的压缩摘要。"""


@dataclass(frozen=True)
class ModelConfig:
    # 官方 Qwen3.8-Flash 上下文窗口；仅用于本地的结束后检查。
    context_window: int = 1_000_000
    compression_ratio: float = 0.8

    def __post_init__(self):
        if type(self.context_window) is not int or self.context_window <= 0:
            raise ValueError("context_window 必须是正整数")
        if not 0 < self.compression_ratio <= 1:
            raise ValueError("compression_ratio 必须在 (0, 1] 内")


def calculate_context_usage(response: dict, context_window: int) -> dict:
    """只使用 Qwen 的 usage：输入 + 输出，不重复累加缓存或思考细分。"""
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("Qwen 响应缺少 usage，无法计算上下文用量")
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if any(type(value) is not int or value < 0 for value in (prompt_tokens, completion_tokens)):
        raise ValueError("usage.prompt_tokens 和 completion_tokens 必须是非负整数")
    if type(context_window) is not int or context_window <= 0:
        raise ValueError("context_window 必须是正整数")
    used = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        "used_tokens": used, "context_window": context_window,
        "remaining_tokens": max(0, context_window - used), "ratio": used / context_window,
        "needs_refresh": False,
    }


def pending_tool_start(history: list[dict]) -> int | None:
    """找到最早一个未收齐全部结果的工具调用消息，作为不可压缩边界。"""
    pending = {}
    for index, message in enumerate(history):
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                pending[call["id"]] = index
        elif message.get("role") == "tool":
            pending.pop(message.get("tool_call_id"), None)
    return min(pending.values()) if pending else None


class PromptManager:
    def __init__(
        self,
        prompt_dir: str | Path = PROMPT_DIR,
        config: ModelConfig | None = None,
    ):
        self.prompt_dir = Path(prompt_dir)
        self.config = config or ModelConfig()

    def _templates(self) -> dict:
        return {name: read_prompt(self.prompt_dir / f"{name}.md") for name in ("system", "normal", "compress")}

    def _request(self, state: dict, tools: list[dict], scene: str = "normal", source=None) -> dict:
        templates = self._templates()
        variables = {
            "tool_descriptions": json.dumps(tools, ensure_ascii=False) if tools else "无",
            **{key: (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
               for key, value in state.items() if key in ("summary", "task_state", "environment", "business_data")},
        }

        def render(template):
            try:
                return Template(template).substitute(variables)
            except (KeyError, ValueError) as error:
                raise ValueError(f"prompt 模板变量错误：{error}") from error

        system = [render(templates["system"]), render(templates[scene])]
        if scene == "normal":
            system.extend(message["content"] for message in state["history"] if message.get("role") == "system")
        messages = [{"role": "system", "content": "\n\n".join(system)}]
        if scene == "compress":
            messages.append({"role": "user", "content": json.dumps(source, ensure_ascii=False)})
        else:
            if any(state[key] for key in ("summary", "task_state", "environment", "business_data")):
                context = "\n".join(f"{label}：\n{variables[key]}" for key, label in (
                    ("summary", "历史摘要"), ("task_state", "任务状态"),
                    ("environment", "环境信息"), ("business_data", "业务数据"),
                ))
                messages.append({"role": "user", "content": "<context_data>\n" + context + "\n</context_data>"})
            messages.extend(deepcopy([message for message in state["history"] if message.get("role") != "system"]))
        request = {"model": "qwen3.8-flash", "messages": messages, "enable_thinking": True}
        if tools and scene == "normal":
            request["tools"] = deepcopy(tools)
        return request

    def build_request(self, session) -> dict:
        # 延迟导入避免 Session 与 PromptManager 的模块循环依赖。
        from session import load_tools

        session.tools = load_tools(session.tools_dir)
        request = self._request(session.state, session.tools)
        session.messages = deepcopy(request["messages"])
        return request

    def finish_turn(self, session, response: dict, request: dict, compress: Callable[[dict], dict]) -> None:
        """普通 loop 正常结束后检查一次；压缩响应不递归触发检查。"""
        usage = calculate_context_usage(response, self.config.context_window)
        session.record_context_usage(usage)
        if usage["ratio"] < self.config.compression_ratio:
            return

        original = session.state
        history = original["history"]
        user_indices = [i for i, message in enumerate(history) if message.get("role") == "user"]
        protected = {i for i, message in enumerate(history) if message.get("role") == "system"}
        if user_indices:
            protected.add(user_indices[-1])
        if history:
            protected.add(len(history) - 1)  # 刚产生的最终回答保留原文。
        pending_start = pending_tool_start(history)
        if pending_start is not None:
            protected.update(range(pending_start, len(history)))
        old = [message for i, message in enumerate(history) if i not in protected]
        if not old:
            return  # 当前问题和受保护的调用关系不能为了压缩而丢弃。

        source = {"previous_summary": original["summary"], "history": old}
        snapshot_id = session.store.snapshot(session.session_id, original, request, usage["used_tokens"])
        try:
            compression_request = self._request(original, [], "compress", source)
            compression_response = compress(compression_request)
            choice = compression_response["choices"][0]
            message = choice["message"]
            summary = message.get("content")
            if choice.get("finish_reason") != "stop" or message.get("tool_calls"):
                raise ContextBudgetError("压缩模型未正常完成摘要")
            if not isinstance(summary, str) or not summary.strip():
                raise ContextBudgetError("压缩摘要为空")
            # 这只是文本压缩效果检查，不把字符长度换算成 token。
            if len(summary.strip()) >= len(json.dumps(source, ensure_ascii=False)):
                raise ContextBudgetError("摘要没有缩短待压缩的文本")
            candidate = deepcopy(original)
            candidate["summary"] = summary.strip()
            candidate["history"] = [message for i, message in enumerate(history) if i in protected]
            # 新上下文的真实 token 数要等下一次普通 Qwen 响应，不能用压缩请求的 usage 代替。
            candidate["context_usage"]["needs_refresh"] = True
            session.store.apply_snapshot(snapshot_id, session.session_id, candidate, None)
        except Exception as error:
            session.store.reject_snapshot(snapshot_id, f"{type(error).__name__}: {error}")
            raise
        session._state = candidate
        session.messages = []  # 下一轮重新组装，避免暴露压缩前的旧输入快照。
