"""持久化会话、prompt 组装入口，以及带 SQLite 日志的模型和工具调用。"""

import json
from copy import deepcopy
from pathlib import Path
from typing import Callable
from uuid import uuid4

from agent_log import AgentLog, DEFAULT_LOG_PATH
from context_store import ContextStore
from prompt_manager import PromptManager


TOOLS_DIR = Path(__file__).resolve().parent / "tools"
MAX_TOOL_CALLS_PER_TURN = 5


class ToolCallLimitError(RuntimeError):
    """本次 run_turn 中，该工具的调用次数已经用完。"""


def load_tools(directory: str | Path = TOOLS_DIR) -> list[dict]:
    """递归读取工具目录，一个 JSON 文件对应一个 function 工具。"""
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"工具目录不存在：{directory}")

    tools = []
    names = {}
    for path in sorted(directory.rglob("*.json")):
        if not path.is_file():
            continue
        try:
            tool = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}：工具 JSON 格式错误：{exc}") from exc

        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise ValueError(f"{path}：工具必须是 type 为 function 的 JSON 对象")
        function = tool.get("function")
        if not isinstance(function, dict):
            raise ValueError(f"{path}：function 必须是对象")
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{path}：function.name 必须是非空字符串")
        if not isinstance(function.get("description"), str):
            raise ValueError(f"{path}：function.description 必须是字符串")
        if not isinstance(function.get("parameters", {}), dict):
            raise ValueError(f"{path}：function.parameters 必须是 JSON Schema 对象")
        if name in names:
            raise ValueError(f"{path}：工具名 {name!r} 与 {names[name]} 重复")
        names[name] = path
        tools.append(tool)
    return tools


class Session:
    """原始 history 独立存档；messages 是每次生成的模型输入。"""

    def __init__(
        self,
        messages: list[dict] | None = None,
        tools_dir: str | Path = TOOLS_DIR,
        log_path: str | Path = DEFAULT_LOG_PATH,
        *,
        environment: object = None,
        business_data: object = None,
        task_state: object = None,
        prompt_manager: PromptManager | None = None,
        session_id: str | None = None,
        title: str = "新会话",
    ):
        self.session_id = session_id or uuid4().hex
        self.tools_dir = Path(tools_dir)
        self.tools = []
        self.messages = []
        self.log = AgentLog(log_path)
        self.store = ContextStore(log_path)
        self.prompt_manager = prompt_manager or PromptManager()
        if session_id is not None:
            if messages is not None:
                raise ValueError("恢复会话时不能同时传入 messages")
            self._state = self.store.load(session_id)
        else:
            self._state = {
                "summary": "", "task_state": task_state,
                "environment": environment, "business_data": business_data,
                "context_usage": None,
                "history": deepcopy(messages) if messages is not None else [],
            }
            self.store.create(self.session_id, self._state, title=title)

    @classmethod
    def restore(cls, session_id: str, **kwargs):
        return cls(session_id=session_id, **kwargs)

    @property
    def state(self) -> dict:
        return deepcopy(self._state)

    @property
    def history(self) -> list[dict]:
        return deepcopy(self._state["history"])

    def add_message(self, message: dict) -> None:
        self.add_messages([message])

    def add_messages(self, messages: list[dict]) -> None:
        candidate = self.state
        messages = deepcopy(messages)
        candidate["history"].extend(messages)
        if candidate.get("context_usage"):
            candidate["context_usage"]["needs_refresh"] = True
        self.store.save(self.session_id, candidate, new_messages=messages)
        self._state = candidate

    def update_context(self, **fields) -> None:
        allowed = {"task_state", "environment", "business_data"}
        if fields.keys() - allowed:
            raise ValueError("只能更新 task_state、environment、business_data")
        candidate = self.state
        candidate.update(deepcopy(fields))
        if candidate.get("context_usage"):
            candidate["context_usage"]["needs_refresh"] = True
        self.store.save(self.session_id, candidate)
        self._state = candidate

    def record_context_usage(self, usage: dict) -> None:
        candidate = self.state
        candidate["context_usage"] = deepcopy(usage)
        self.store.save(self.session_id, candidate)
        self._state = candidate

    def build_request(self) -> dict:
        """每次从模板、工具 JSON 和原始上下文重新组装请求。"""
        return self.prompt_manager.build_request(self)

    def call_llm(self, send_request: Callable[[dict], dict], *, request: dict | None = None, scene: str = "normal") -> dict:
        """发送当前请求并记录完整 JSON 响应；send_request 负责实际 HTTP 调用。"""
        request = self.build_request() if request is None else deepcopy(request)

        def invoke():
            response = send_request(request)
            if not isinstance(response, dict):
                raise TypeError("send_request 必须返回完整响应的 dict，不能返回流式迭代器")
            return response

        return self.log.run(
            session_id=self.session_id,
            kind="llm",
            name=request["model"],
            request=request,
            operation=invoke,
            scene=scene,
        )

    def call_tool(
        self,
        tool_call: dict,
        execute: Callable,
        *,
        call_counts: dict[str, int] | None = None,
        return_errors: bool = False,
    ) -> object:
        """同步调用工具并记录日志；loop 可提供本轮计数并接收错误结果。"""
        function = tool_call["function"]

        def invoke():
            name = function["name"]
            if call_counts is not None:
                count = call_counts.get(name, 0)
                if count >= MAX_TOOL_CALLS_PER_TURN:
                    raise ToolCallLimitError(
                        f"工具 {name} 在本次 loop 中已达到 {MAX_TOOL_CALLS_PER_TURN} 次调用上限，"
                        "本次未执行，请使用其他工具或根据已有信息回答。"
                    )
                # 在参数解析前计数，参数错误和执行失败也消耗一次机会。
                call_counts[name] = count + 1
            if not isinstance(function.get("arguments"), str):
                raise ValueError("工具 arguments 必须是 JSON 字符串")
            arguments = json.loads(function["arguments"])
            if not isinstance(arguments, dict):
                raise ValueError("工具 arguments 必须是 JSON 对象")
            return execute(**arguments)

        return self.log.run(
            session_id=self.session_id,
            kind="tool",
            name=function["name"],
            tool_call_id=tool_call["id"],
            request=tool_call,
            operation=invoke,
            on_error=(
                lambda error: {"error": {"type": type(error).__name__, "message": str(error)}}
            ) if return_errors else None,
        )
