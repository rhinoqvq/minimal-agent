"""LLM 和工具调用的 SQLite 日志，仅使用标准库。"""

import json
import sqlite3
import traceback
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable, TypeVar


DEFAULT_LOG_PATH = Path(__file__).resolve().parent / "agent_logs.sqlite3"
T = TypeVar("T")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class AgentLog:
    """每次调用保存一行；在首次调用时创建数据库，不长期持有连接。"""

    def __init__(self, path: str | Path = DEFAULT_LOG_PATH):
        self.path = Path(path)

    def run(
        self,
        *,
        session_id: str,
        kind: str,
        name: str,
        request: object,
        operation: Callable[[], T],
        tool_call_id: str | None = None,
        on_error: Callable[[Exception], T] | None = None,
        scene: str | None = None,
    ) -> T:
        """记录一次同步调用；可在错误落库后返回 on_error 生成的结果。

        request 和调用结果须为可 JSON 序列化的值。
        """
        request_json = json.dumps(request, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS calls (
                    id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('llm', 'tool')),
                    name TEXT NOT NULL,
                    tool_call_id TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    duration_ms REAL,
                    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'error')),
                    request_json TEXT NOT NULL,
                    response_json TEXT,
                    error_type TEXT,
                    error_message TEXT,
                    error_traceback TEXT,
                    scene TEXT
                )
            """)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(calls)")}
            if "scene" not in columns:
                connection.execute("ALTER TABLE calls ADD COLUMN scene TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS calls_session ON calls (session_id, id)"
            )
            cursor = connection.execute("""
                INSERT INTO calls
                    (session_id, kind, name, tool_call_id, started_at, status, request_json, scene)
                VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
            """, (session_id, kind, name, tool_call_id, _now(), request_json, scene))
            call_id = cursor.lastrowid

        # 上面的事务已提交，运行中的调用也能被其他连接读到。
        started = perf_counter()
        try:
            result = operation()
            response_json = json.dumps(result, ensure_ascii=False)
        except BaseException as error:
            recover = on_error is not None and isinstance(error, Exception)
            try:
                response_json = None
                if recover:
                    result = on_error(error)
                    response_json = json.dumps(result, ensure_ascii=False)
                self._finish(
                    call_id, started, "error",
                    response_json=response_json,
                    error_type=type(error).__name__,
                    error_message=str(error),
                    error_traceback=traceback.format_exc(),
                )
            except Exception as log_error:
                # 写日志失败也不能掩盖原始调用异常。
                raise error from log_error
            if recover:
                return result
            raise

        self._finish(call_id, started, "success", response_json=response_json)
        return result

    def _finish(
        self,
        call_id: int,
        started: float,
        status: str,
        response_json: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        error_traceback: str | None = None,
    ) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("""
                UPDATE calls SET
                    finished_at = ?, duration_ms = ?, status = ?, response_json = ?,
                    error_type = ?, error_message = ?, error_traceback = ?
                WHERE id = ?
            """, (
                _now(), (perf_counter() - started) * 1000, status, response_json,
                error_type, error_message, error_traceback, call_id,
            ))
