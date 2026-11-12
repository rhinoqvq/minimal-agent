"""使用已经组装好的 messages 运行到模型 stop，返回最终正文。"""

import json
from copy import deepcopy
from typing import Callable

from session import Session
from prompt_manager import pending_tool_start
from tool_functions import get_builtin_tool_handlers


class MaxStepsExceededError(RuntimeError):
    """本轮 LLM 请求步数已用完，模型尚未返回最终回答。"""


def run_turn(
    session: Session,
    send_request: Callable[[dict], dict],
    tool_handlers: dict[str, Callable] | None = None,
    *,
    max_steps: int = 5,
) -> str:
    """每步 make prompt；正常结束后根据 Qwen usage 检查并按需压缩一次。"""
    if type(max_steps) is not int or max_steps < 1:
        raise ValueError("max_steps 必须是正整数")
    handlers = tool_handlers if tool_handlers is not None else get_builtin_tool_handlers(session)
    tool_call_counts: dict[str, int] = {}
    if pending_tool_start(session.history) is not None:
        raise ValueError("会话存在未完成的工具调用，请先补齐结果；不会自动重复执行工具")

    for _ in range(max_steps):
        request = session.prompt_manager.build_request(session)
        declared_names = {tool["function"]["name"] for tool in session.tools}
        response = session.call_llm(send_request, request=request)
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("LLM 响应缺少有效的 choices[0]")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError("LLM 响应缺少有效的 assistant message")
        message = deepcopy(message)
        finish_reason = choice.get("finish_reason")

        if finish_reason == "stop":
            if message.get("tool_calls"):
                raise ValueError("finish_reason 为 stop，但仍包含待执行的 tool_calls")
            content = message.get("content")
            if not isinstance(content, str):
                raise ValueError("finish_reason 为 stop 时，content 必须是字符串")
            session.add_message(message)
            session.prompt_manager.finish_turn(
                session, response, request,
                lambda body: session.call_llm(send_request, request=body, scene="compress"),
            )
            return content

        if finish_reason == "length":
            raise RuntimeError("LLM 输出被截断（finish_reason=length），没有最终结果")
        if finish_reason != "tool_calls":
            raise ValueError(f"未知的 finish_reason：{finish_reason!r}")

        calls = message.get("tool_calls")
        _validate_tool_calls(calls)
        session.add_message(message)
        for call in calls:
            name = call["function"]["name"]

            def execute(**arguments):
                # 分派也在 call_tool 的日志范围内，未知工具不会绕过日志。
                if name not in declared_names:
                    raise ValueError(f"模型请求了未声明的工具：{name}")
                if name not in handlers:
                    raise ValueError(f"工具尚未绑定执行函数：{name}")
                return handlers[name](**arguments)

            result = session.call_tool(
                call, execute, call_counts=tool_call_counts, return_errors=True,
            )
            session.add_message({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": result if isinstance(result, str) else json.dumps(result, ensure_ascii=False),
            })

    raise MaxStepsExceededError(
        f"已达到本轮 LLM 请求步数上限 max_steps={max_steps}，模型尚未返回 stop"
    )


def _validate_tool_calls(calls: object) -> None:
    """执行前检查调用结构，参数 JSON 的解析由带日志的 call_tool 完成。"""
    if not isinstance(calls, list) or not calls:
        raise ValueError("finish_reason 为 tool_calls 时，tool_calls 必须是非空数组")
    ids = set()
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function":
            raise ValueError("tool_call 必须是 function 类型的对象")
        call_id = call.get("id")
        if not isinstance(call_id, str) or not call_id:
            raise ValueError("tool_call.id 必须是非空字符串")
        if call_id in ids:
            raise ValueError(f"同一轮 tool_call.id 重复：{call_id}")
        ids.add(call_id)
        function = call.get("function")
        if not isinstance(function, dict):
            raise ValueError("tool_call.function 必须是对象")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("tool_call.function.name 必须是非空字符串")
