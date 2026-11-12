"""绑定当前会话的 SQLite 待办工具；session_id 不接受模型输入。"""

from context_store import ContextStore


STATUSES = ("待办", "进行中", "已完成")


class TodoTool:
    def __init__(self, store: ContextStore, session_id: str):
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id 必须是非空字符串，由后端提供")
        self.store = store
        self.session_id = session_id

    def __call__(
        self,
        action: int,
        id: int | None = None,
        content: str | None = None,
        stauts: str | None = None,
    ) -> dict | list[dict]:
        """0 新增、1 列表、2 按 id 查询、3 修改、4 删除。"""
        if type(action) is not int or action not in (0, 1, 2, 3, 4):
            raise ValueError("action 必须是整数：0 新增、1 列表、2 查询单条、3 修改、4 删除")
        if action in (2, 3, 4):
            if type(id) is not int or id < 1 or id > 2**63 - 1:
                raise ValueError("查询、修改、删除必须提供有效的正整数 id")
        elif id is not None:
            raise ValueError("新增和列表不接受 id；新增 id 由数据库自动生成")
        if action in (1, 2, 4) and (content is not None or stauts is not None):
            raise ValueError("列表、查询、删除不接受 content 或 stauts")
        if action == 0 or content is not None:
            if not isinstance(content, str) or not content.strip():
                raise ValueError("content 必须是非空字符串")
        if stauts is not None and stauts not in STATUSES:
            raise ValueError("stauts 只能是：待办、进行中、已完成")
        if action == 3 and content is None and stauts is None:
            raise ValueError("修改时至少提供 content 或 stauts 中的一个")

        with self.store.connect() as db:
            # 不允许给已经删除或不存在的会话创建孤立待办。
            if not db.execute("SELECT 1 FROM session_contexts WHERE session_id = ?", (self.session_id,)).fetchone():
                raise ValueError("当前会话不存在")
            if action == 0:
                cursor = db.execute(
                    "INSERT INTO todo_tool (session_id, content, stauts) VALUES (?, ?, ?)",
                    (self.session_id, content, stauts if stauts is not None else "待办"),
                )
                id = cursor.lastrowid
            elif action == 1:
                rows = db.execute(
                    "SELECT id, session_id, content, stauts FROM todo_tool WHERE session_id = ? ORDER BY id",
                    (self.session_id,),
                ).fetchall()
                return [dict(row) for row in rows]
            elif action == 3:
                cursor = db.execute(
                    "UPDATE todo_tool SET content = COALESCE(?, content), stauts = COALESCE(?, stauts) "
                    "WHERE session_id = ? AND id = ?",
                    (content, stauts, self.session_id, id),
                )
                if cursor.rowcount != 1:
                    raise ValueError(f"当前会话中不存在 id={id} 的待办")
            elif action == 4:
                cursor = db.execute("DELETE FROM todo_tool WHERE session_id = ? AND id = ?", (self.session_id, id))
                if cursor.rowcount != 1:
                    raise ValueError(f"当前会话中不存在 id={id} 的待办")
                return {"id": id, "deleted": True}

            row = db.execute(
                "SELECT id, session_id, content, stauts FROM todo_tool WHERE session_id = ? AND id = ?",
                (self.session_id, id),
            ).fetchone()
            if row is None:
                raise ValueError(f"当前会话中不存在 id={id} 的待办")
            return dict(row)
