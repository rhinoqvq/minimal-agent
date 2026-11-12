"""保存会话元信息、当前上下文、完整消息存档，以及压缩前快照。"""

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ContextStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS session_contexts (
                    session_id TEXT PRIMARY KEY, state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS context_messages (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL, created_at TEXT NOT NULL,
                    message_json TEXT NOT NULL, UNIQUE(session_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS context_snapshots (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    state_json TEXT NOT NULL, request_json TEXT NOT NULL,
                    before_tokens INTEGER NOT NULL, after_tokens INTEGER,
                    status TEXT NOT NULL, error TEXT
                );
                CREATE TABLE IF NOT EXISTS todo_tool (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    stauts TEXT NOT NULL DEFAULT '待办'
                        CHECK (stauts IN ('待办', '进行中', '已完成'))
                );
                CREATE INDEX IF NOT EXISTS todo_tool_session ON todo_tool (session_id, id);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(session_contexts)")}
            if "title" not in columns:
                db.execute("ALTER TABLE session_contexts ADD COLUMN title TEXT NOT NULL DEFAULT '新会话'")

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    def create(self, session_id: str, state: dict, title: str = "新会话") -> None:
        timestamp = now()
        with self.connect() as db:
            db.execute("""INSERT INTO session_contexts
                       (session_id, state_json, created_at, updated_at, title) VALUES (?, ?, ?, ?, ?)""",
                       (session_id, dumps(state), timestamp, timestamp, title))
            self._archive(db, session_id, state["history"])

    def list_sessions(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""SELECT session_id, title, created_at, updated_at FROM session_contexts
                                 ORDER BY updated_at DESC, session_id""").fetchall()
        return [dict(row) for row in rows]

    def metadata(self, session_id: str) -> dict:
        with self.connect() as db:
            return self._metadata(db, session_id)

    def _metadata(self, db, session_id):
        row = db.execute("""SELECT session_id, title, created_at, updated_at FROM session_contexts
                            WHERE session_id = ?""", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"会话不存在：{session_id}")
        return dict(row)

    def rename(self, session_id: str, title: str) -> dict:
        with self.connect() as db:
            cursor = db.execute("UPDATE session_contexts SET title = ?, updated_at = ? WHERE session_id = ?",
                                (title, now(), session_id))
            if cursor.rowcount != 1:
                raise KeyError(f"会话不存在：{session_id}")
            return self._metadata(db, session_id)

    def delete(self, session_id: str) -> None:
        """在同一事务内删除会话及其所有运行数据，不影响其他会话。"""
        with self.connect() as db:
            cursor = db.execute("DELETE FROM session_contexts WHERE session_id = ?", (session_id,))
            if cursor.rowcount != 1:
                raise KeyError(f"会话不存在：{session_id}")
            db.execute("DELETE FROM context_messages WHERE session_id = ?", (session_id,))
            db.execute("DELETE FROM context_snapshots WHERE session_id = ?", (session_id,))
            db.execute("DELETE FROM todo_tool WHERE session_id = ?", (session_id,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'calls'").fetchone():
                db.execute("DELETE FROM calls WHERE session_id = ?", (session_id,))

    def load(self, session_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT state_json FROM session_contexts WHERE session_id = ?",
                             (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"会话不存在：{session_id}")
        return json.loads(row["state_json"])

    def context_status(self, session_id: str) -> dict:
        """只返回窗口用量和最近压缩状态，不向状态轮询发送历史或摘要。"""
        with self.connect() as db:
            row = db.execute("SELECT state_json FROM session_contexts WHERE session_id = ?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(f"会话不存在：{session_id}")
            snapshot = db.execute("""SELECT id, status, created_at FROM context_snapshots
                                     WHERE session_id = ? ORDER BY id DESC LIMIT 1""", (session_id,)).fetchone()
        return {"context_usage": json.loads(row["state_json"]).get("context_usage"),
                "compression": dict(snapshot) if snapshot else None}

    def save(self, session_id: str, state: dict, new_messages: list[dict] = ()) -> None:
        with self.connect() as db:
            self._save(db, session_id, state)
            self._archive(db, session_id, new_messages)

    def _save(self, db, session_id, state):
        cursor = db.execute("UPDATE session_contexts SET state_json = ?, updated_at = ? WHERE session_id = ?",
                            (dumps(state), now(), session_id))
        if cursor.rowcount != 1:
            raise KeyError(f"会话不存在：{session_id}")

    def _archive(self, db, session_id, messages):
        sequence = db.execute("SELECT COALESCE(MAX(sequence), 0) FROM context_messages WHERE session_id = ?",
                              (session_id,)).fetchone()[0]
        for offset, message in enumerate(messages, start=1):
            db.execute("""INSERT INTO context_messages (session_id, sequence, created_at, message_json)
                          VALUES (?, ?, ?, ?)""", (session_id, sequence + offset, now(), dumps(message)))

    def archive(self, session_id: str) -> list[dict]:
        with self.connect() as db:
            self._metadata(db, session_id)
            rows = db.execute("SELECT * FROM context_messages WHERE session_id = ? ORDER BY sequence",
                              (session_id,)).fetchall()
        return [{"sequence": row["sequence"], "created_at": row["created_at"],
                 "message": json.loads(row["message_json"])} for row in rows]

    def snapshot(self, session_id: str, state: dict, request: dict, tokens: int) -> int:
        with self.connect() as db:
            cursor = db.execute("""INSERT INTO context_snapshots
                (session_id, created_at, state_json, request_json, before_tokens, status)
                VALUES (?, ?, ?, ?, ?, 'pending')""",
                (session_id, now(), dumps(state), dumps(request), tokens))
            return cursor.lastrowid

    def reject_snapshot(self, snapshot_id: int, error: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE context_snapshots SET status = 'rejected', error = ? WHERE id = ?",
                       (error, snapshot_id))

    def apply_snapshot(self, snapshot_id: int, session_id: str, state: dict, tokens: int | None) -> None:
        with self.connect() as db:
            snapshot = db.execute("SELECT state_json FROM context_snapshots WHERE id = ? AND session_id = ?",
                                  (snapshot_id, session_id)).fetchone()
            if snapshot is None:
                raise ValueError("压缩快照不存在")
            current = db.execute("SELECT state_json FROM session_contexts WHERE session_id = ?",
                                 (session_id,)).fetchone()
            if current is None or current["state_json"] != snapshot["state_json"]:
                raise RuntimeError("压缩期间会话已更新，保留新消息，取消替换上下文")
            cursor = db.execute("""UPDATE context_snapshots SET status = 'applied', after_tokens = ?
                                   WHERE id = ? AND session_id = ? AND status = 'pending'""",
                                (tokens, snapshot_id, session_id))
            if cursor.rowcount != 1:
                raise ValueError("压缩快照不存在或已处理")
            self._save(db, session_id, state)
