"""本地单进程会话 API：按固定 session_id 读取历史并继续 Agent。"""

import logging
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Callable

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_log import DEFAULT_LOG_PATH
from agent_loop import MaxStepsExceededError, run_turn
from context_store import ContextStore
from prompt_manager import PromptManager, pending_tool_start
from qwen_client import QwenClient
from session import Session, TOOLS_DIR


logger = logging.getLogger(__name__)


class SessionTitle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        if not value.strip():
            raise ValueError("会话名称不能为空")
        return value.strip()


class NewSession(SessionTitle):
    title: str = Field(default="新会话", min_length=1, max_length=200)


class UserMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def nonempty_content(cls, value):
        if not value.strip():
            raise ValueError("消息不能为空")
        return value  # 用户原文原样保存，保留空格和换行。


def create_app(
    log_path: str | Path = DEFAULT_LOG_PATH,
    *,
    tools_dir: str | Path = TOOLS_DIR,
    prompt_manager: PromptManager | None = None,
    send_request: Callable[[dict], dict] | None = None,
    tool_handlers: dict[str, Callable] | None = None,
) -> FastAPI:
    """uvicorn api:create_app --factory；测试可注入发送函数和临时数据库。"""
    app = FastAPI(title="Minimal Agent", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8001", "http://localhost:8001"],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    store = ContextStore(log_path)
    manager = prompt_manager or PromptManager()
    session_options = {"log_path": log_path, "tools_dir": tools_dir, "prompt_manager": manager}
    busy_sessions: set[str] = set()
    busy_guard = Lock()
    running_turns: dict[str, int] = {}  # 本轮开始前的快照 ID，区分旧的中断压缩。

    def context_status(session_id):
        with busy_guard:
            result = store.context_status(session_id)
            running = session_id in busy_sessions
            baseline = running_turns.get(session_id)
            snapshot = result["compression"]
            compressing = bool(snapshot and snapshot["status"] == "pending"
                               and baseline is not None and snapshot["id"] > baseline)
            if snapshot and snapshot["status"] == "pending" and not compressing:
                snapshot["status"] = "interrupted"
        return {"session_id": session_id, "running": running,
                "phase": "compressing" if compressing else "running" if running else "idle",
                "context_window": manager.config.context_window,
                "compression_ratio": manager.config.compression_ratio, **result}

    @contextmanager
    def exclusive(session_id):
        # 单进程内同一会话只能执行一个写操作，读取历史不需要等模型结束。
        with busy_guard:
            if session_id in busy_sessions:
                raise HTTPException(409, detail={"code": "session_busy", "session_id": session_id,
                                                 "message": "会话正在运行，请完成后再操作"})
            busy_sessions.add(session_id)
        try:
            yield
        finally:
            with busy_guard:
                busy_sessions.remove(session_id)

    @contextmanager
    def existing_session():
        try:
            yield
        except KeyError:
            raise HTTPException(404, detail={"code": "session_not_found", "message": "会话不存在"}) from None

    @app.get("/sessions")
    def list_sessions():
        return {"sessions": store.list_sessions()}

    @app.post("/sessions", status_code=201)
    def create_session(body: NewSession):
        session = Session(title=body.title, **session_options)
        return store.metadata(session.session_id)

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str):
        with existing_session():
            return store.metadata(session_id)

    @app.get("/sessions/{session_id}/messages")
    def get_messages(session_id: str):
        with existing_session():
            return {**context_status(session_id), "messages": store.archive(session_id)}

    @app.get("/sessions/{session_id}/status")
    def get_context_status(session_id: str):
        with existing_session():
            return context_status(session_id)

    @app.patch("/sessions/{session_id}")
    def rename_session(session_id: str, body: SessionTitle):
        with exclusive(session_id), existing_session():
            return store.rename(session_id, body.title)

    @app.delete("/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str):
        with exclusive(session_id), existing_session():
            store.delete(session_id)
        return Response(status_code=204)

    @app.post("/sessions/{session_id}/messages")
    def send_message(session_id: str, body: UserMessage):
        with exclusive(session_id):
            with existing_session():
                session = Session.restore(session_id, **session_options)
            if pending_tool_start(session.history) is not None:
                raise HTTPException(409, detail={"code": "pending_tool_calls", "session_id": session_id,
                                                 "message": "会话有未完成的工具调用，请先补齐结果"})
            client = None
            if send_request is None:
                try:
                    client = QwenClient()
                except ValueError:
                    raise HTTPException(503, detail={"code": "model_not_configured",
                                                     "message": "请先在服务端 .env 中配置 QWEN_API_KEY"}) from None
            try:
                with busy_guard:
                    previous = store.context_status(session_id)["compression"]
                    running_turns[session_id] = previous["id"] if previous else 0
                session.add_message({"role": "user", "content": body.content})
                content = run_turn(session, send_request if send_request is not None else client,
                                   tool_handlers=tool_handlers)
                return {"session_id": session_id, "content": content,
                        "context_usage": session.state["context_usage"]}
            except MaxStepsExceededError:
                raise HTTPException(409, detail={"code": "max_steps_exceeded", "session_id": session_id,
                                                 "message": "本轮已达 5 步上限，已保存历史，可发送新消息继续"}) from None
            except Exception as error:
                logger.exception("Agent run failed for session %s", session_id)
                # 原始异常及请求留在日志，不把 SDK 异常文本或请求头发给前端。
                raise HTTPException(502, detail={"code": "agent_run_failed", "session_id": session_id,
                                                 "error_type": type(error).__name__,
                                                 "message": "运行失败，请重新读取已保存的历史并查看服务端日志"}) from None
            finally:
                with busy_guard:
                    running_turns.pop(session_id, None)
                if client is not None:
                    client.close()

    return app
