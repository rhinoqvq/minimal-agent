# 工具声明

在这个目录中添加工具的 `.json` 文件。一个文件对应一个完整的 Qwen function 工具对象，文件名可以与工具名相同。

每次 make prompt 时递归读取所有 `.json` 文件，按路径排序，用同一份定义生成 prompt 中的工具说明和请求体的 `tools` 数组。非 JSON 文件不会加载。工具名必须唯一；JSON 或基本结构有误时，请求组装失败并指出文件。

目前已提供 `calculate.json`、`search.json` 和 `todolist.json`。计算与搜索函数位于 `tool_functions.py`，待办实现为 `todo_tool.TodoTool`，安装项目依赖后默认后端即可调用。

例如模型请求计算：

```json
{
  "expression": "(12.5 + 7.5) * 3 / 2"
}
```

工具返回：

```json
{"expression": "(12.5 + 7.5) * 3 / 2", "result": "30"}
```

支持十进制数字、科学计数法、括号、正负号、四则运算和 `**` 整数幂。采用 28 位有效数字的十进制计算；例如 `0.1 + 0.2` 返回 `"0.3"`。不支持变量、函数、百分号、整除、位运算或 Python 代码。表达式最多 500 字符、128 个 AST 节点；幂指数限定为 -1000 至 1000，数值超出支持范围会返回错误。

结果使用字符串，避免 JSON 数字或前端浮点数再次损失精度。除零、无效表达式等异常经过原有工具调用入口记录到 SQLite，并以对应的 tool 错误消息返回模型，模型可在剩余步数和次数范围内自行修正重试。

搜索工具只接收查询字符串：

```json
{"query": "东京国立博物馆 官方网站"}
```

后端调用 `DDGS().text(query, region="jp-ja", backend="auto", max_results=10)`。搜索引擎自动选择，地区为日本（日语），最多 10 条结果。固定使用 DDGS 9.16.1，其他参数沿用库默认值，不需要搜索 API key。

返回值直接使用 DDGS 的 Python 字典列表，不额外包一层对象，也不提前序列化。例如以下示意结果：

```json
[{"title": "示例网页标题", "href": "https://example.com/article", "body": "搜索摘要内容。"}]
```

原有 loop 把字典列表转为 JSON 字符串，填入 `role="tool"` 消息的 `content`，保留对应 `tool_call_id`，再通过 `session.add_message()` 保存；下一步组装 prompt 时模型能读取结果。`body` 仅为搜索摘要。超时等异常直接交给现有工具错误处理，不在搜索函数中自行重试；每轮最多 5 次 search 调用。

待办工具使用现有 SQLite 中的 `todo_tool` 表，仅有 `id`、`session_id`、`content`、`stauts` 四个字段。`id` 由数据库自增生成；`session_id` 必填，由后端绑定当前会话，不出现在模型可填写的参数中。模型填写 `content` 和状态 `stauts`，状态仅允许“待办”“进行中”“已完成”。

| action | 操作 | 模型填写的其他字段 | 返回值 |
| --- | --- | --- | --- |
| `0` | 新增 add | `content` 必填，`stauts` 可选（默认待办），不能填 `id` | 新记录对象 |
| `1` | 列表 list | 无 | 当前会话的记录数组，按 id 升序；没有记录返回 `[]` |
| `2` | 按 id 查询单条 | `id` 必填 | 对应记录对象 |
| `3` | 修改 update | `id` 必填，`content` / `stauts` 至少填一个 | 修改后的记录对象 |
| `4` | 删除 delete | `id` 必填 | `{"id": 1, "deleted": true}` |

新增参数示例：

```json
{"action": 0, "content": "整理搜索结果", "stauts": "待办"}
```

返回示例（id 和 session_id 使用实际生成的值）：

```json
{"id": 1, "session_id": "当前会话id", "content": "整理搜索结果", "stauts": "待办"}
```

把上述待办改为进行中：`{"action": 3, "id": 1, "stauts": "进行中"}`。修改未传入的字段保持原值。查询、修改和删除均以当前 session_id 和 id 同时匹配；其他会话的 id 与不存在的 id 都返回“当前会话中不存在”错误。参数错误和执行错误沿用原有日志、错误回传与次数限制，所有 action 合计每轮最多 5 次。待办持久化保存在表中，压缩聊天历史不会删除待办；删除会话会在同一事务中删除它的待办。

工具文件修改后，下一次组装请求会读取新内容。JSON 负责描述工具；计算和搜索使用 `tool_functions.BUILTIN_TOOL_HANDLERS`，`get_builtin_tool_handlers(session)` 每轮再绑定当前会话的 `todolist`。添加新的内置工具时，需要同时增加 JSON 和实际函数映射；只放 JSON 不会自动执行代码。

也可通过 `run_turn(..., tool_handlers={"工具名": Python函数})` 或 `create_app(tool_handlers=...)` 显式指定映射；传入的映射替代内置映射，空字典会禁用全部默认实现。工具必须同时有 JSON 声明和对应函数才会执行。输入参数解析后通过关键字参数传入，返回值须可 JSON 序列化。加载器检查基本结构，具体工具检查自身输入，暂不实现完整 JSON Schema 参数校验。
