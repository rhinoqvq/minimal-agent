# Minimal Agent

一个可在本地运行的最小聊天 Agent：使用 Qwen Chat Completions 接口，支持多轮对话、工具调用、独立会话、基础上下文压缩、SQLite 持久化和调用日志。前端使用 React，后端使用 FastAPI。

默认模型在代码中配置为 `qwen3.8-flash`，开启 `enable_thinking`。模型请求为非流式；前端收到完整回答后播放打字机动画。

## 快速开始

需要 Python 3.10+、Node.js 20.19+ 或 22.12+。以下为 macOS / Linux 命令，均在项目根目录执行。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci
cp .env.example .env
```

编辑本地 `.env`：

```dotenv
QWEN_API_KEY=your_api_key_here
QWEN_BASE_URL=https://maas.qianwenaiapi.com/compatible-mode/v1
```

`QWEN_BASE_URL` 应与所使用的密钥和服务入口对应。环境变量会覆盖 `.env` 中的同名配置。真实密钥只放在本地 `.env` 或环境变量中，不写入代码和前端配置；`.env` 已被 Git 忽略。

在两个终端分别启动：

```sh
# 终端一：后端，必须使用单进程
.venv/bin/uvicorn api:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

```sh
# 终端二：构建并启动前端
npm start
```

- 聊天页面：[http://127.0.0.1:8001](http://127.0.0.1:8001)
- 后端接口文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- 前端开发模式：停止原 8001 服务后运行 `npm run dev`。

前端支持新建、切换、重命名、删除会话，以及查看工具调用详情和上下文用量。`Enter` 发送，`Shift + Enter` 换行。待办通过聊天操作，无单独的待办面板。

可依次尝试：

1. “请用计算工具计算 `(12.5 + 7.5) * 3 / 2`。”
2. “把刚才的结果再乘以 2。”
3. “搜索 Python 官方教程，给我链接。”
4. “新增待办：阅读 Python 教程。”
5. “列出当前会话的待办，把刚才那条改为进行中。”

另建一个会话再查询待办，可以验证会话隔离。真实对话需要有效的模型密钥，网页搜索需要网络；自动化测试使用模拟模型。

## 系统设计

```text
React 聊天页面
    │ HTTP：session_id + 用户消息
    ▼
FastAPI → 恢复 Session → 追加用户原文
    ▼
PromptManager：Markdown 模板 + 当前摘要/状态 + 历史 + 工具 Schema
    ▼
QwenClient → 解析模型返回
    ├─ tool_calls → 工具分派 → 执行 → 保存 tool 结果 → 下一步模型请求
    └─ stop       → 保存最终回答 → 检查用量/必要时压缩 → 返回前端

SQLite：当前上下文、完整消息存档、压缩快照、调用日志、会话待办
```

| 文件 / 目录 | 职责 |
| --- | --- |
| `api.py` | 会话 HTTP API、输入校验和单进程内的会话互斥 |
| `session.py` | 会话创建/恢复、消息追加、工具定义加载和调用入口 |
| `agent_loop.py` | 解析回复、分派工具、回填结果和限制循环步数 |
| `prompt_manager.py` / `prompt_store/` | Prompt 组装、用量统计和压缩 |
| `qwen_client.py` | 模型 HTTP 请求和密钥配置 |
| `tool_functions.py` / `todo_tool.py` | 计算、搜索和待办的执行逻辑 |
| `tools/` | 工具名称、描述和参数 JSON Schema |
| `context_store.py` / `agent_log.py` | SQLite 存储和调用日志 |
| `frontend/` | 聊天界面、会话状态和打字机动画 |
| `tests/` | 后端单元/接口测试和浏览器交互测试 |

## Loop、工具注册与异常处理

每步重新组装请求，保留模型返回的 `content`、`reasoning_content`、`tool_calls` 等消息字段。通过 `finish_reason` 和结构化字段决定继续调用工具还是结束，不解析自定义的 Final Answer 标签。

- `tool_calls`：先保存 assistant 调用消息，再逐个执行工具。结果以 `role: "tool"` 和对应 `tool_call_id` 保存，下一次请求携带这些结果。
- `stop`：保存正文，完成用量检查和必要的压缩后返回本轮结果。
- `length`、无效响应、模型请求失败：明确报错，不把未完成输出当作最终答案。
- 参数解析、未知工具和工具执行错误：记录日志，并以 `{"error":{"type":"...","message":"..."}}` 回填给模型，让模型在剩余额度内修正。

默认每次 `run_turn()` 最多发起 **5 次普通模型请求**；每种工具最多尝试 **5 次**，失败及参数错误也计数。达到步数上限后保留已经完成的调用和结果，用户可继续发消息。用户累计聊天轮数不设固定上限。

每次组装请求递归读取 `tools/**/*.json`，校验基本结构及名称唯一性。工具还必须绑定到实际 Python 函数；仅新增 JSON 不会自动执行代码。当前没有通用的完整 JSON Schema 校验器，具体工具自行检查参数。注册方式及参数详见 [工具说明](tools/README.md)。

| 工具 | 功能 | 实现要点 |
| --- | --- | --- |
| `calculate` | 四则运算、括号、正负号、整数幂 | AST 白名单解析和 Decimal 运算，不执行任意 Python 代码；限制表达式规模，结果用字符串保留精度 |
| `search` | 网页搜索 | 使用 DDGS 9.16.1，固定 `backend="auto"`、`region="jp-ja"`、`max_results=10`；返回标题、链接、摘要，不保证是某个指定搜索引擎，也不抓取网页全文 |
| `todolist` | 会话待办增删改查 | `session_id` 由后端绑定，模型不能指定或覆盖；数据持久化到 SQLite |

待办参数 `action`：`0` 新增、`1` 列表、`2` 按 ID 查询、`3` 修改、`4` 删除。新增时数据库生成 `id`；查询、修改、删除按当前 `session_id` 和 `id` 同时匹配。状态字段沿用代码中的 `stauts` 拼写，值为“待办 / 进行中 / 已完成”。

## Prompt 管理与 Memory

项目有普通任务和压缩两个场景，共用三个 Markdown 模板：

- `prompt_store/system.md`：通用规则，要求依据实际工具结果回答。
- `prompt_store/normal.md`：普通任务指导及工具说明。
- `prompt_store/compress.md`：要求将旧对话整理为摘要。

每次请求重新读取模板和工具 JSON。同一份工具定义既用于 prompt 说明，也放入请求体 `tools`。模板支持 `${tool_descriptions}`、`${summary}`、`${task_state}`、`${environment}`、`${business_data}`；缺失模板或变量会报错。

本项目的 memory 是**当前会话内的持久化上下文和摘要**，没有跨会话的用户画像、向量检索或自动检索完整历史的模块。

**召回时机**：收到携带 `session_id` 的新消息时，从 SQLite 加载该会话的最新状态；每次模型调用前再组装摘要与保留历史。原始记录可由历史 API 查看，但不会在压缩后全部重新注入模型。

**放置方式**：

1. 首条 `system` 消息放通用规则、普通场景模板及会话额外系统规则。
2. 有摘要或状态数据时，在历史之前插入一条标为 `<context_data>` 的 `user` 消息，包含历史摘要、任务状态、环境信息和业务数据，并明确这些是任务资料。
3. 随后按原顺序放入保留的 `user`、`assistant` 和 `tool` 历史，保留工具关联 ID，支持纯对话和工具追问。

待办另存于 `todo_tool`，不会随聊天压缩删除。模型需要待办信息时调用 `todolist`，结果通过 `tool` 消息进入上下文。所有操作仅作用于当前会话。

## 上下文用量与基础压缩

一次普通 loop 正常 `stop` 并保存回答后，使用最后一次普通响应的 `usage` 检查用量：

```text
used_tokens = prompt_tokens + completion_tokens
ratio = used_tokens / context_window
```

代码中的窗口配置默认是 `1_000_000`，压缩阈值为 `0.8`。这些是本项目的本地配置，未作为模型请求参数发送；切换模型时需要核对并修改对应配置。测试可通过 `ModelConfig(context_window=4000)` 设置较小窗口。

达到阈值后：

1. 保存压缩前快照和请求。
2. 将已有摘要和可压缩历史交给压缩场景，压缩请求不带工具。
3. 保留最新用户问题、刚生成的最终回答、系统消息和未完成工具调用组的原文。
4. 摘要须正常完成、非空且短于原材料，才原子替换当前上下文；失败或压缩期间状态已变化时保留原数据。

完整原始消息始终另行存档。压缩后的准确 token 用量要等下一次普通响应更新，界面以 `needs_refresh` 表示“待更新”，不使用压缩请求的 token 数冒充新上下文用量。

当前只在正常结束后检查，不在 loop 中做请求前 token 估算，也没有 context-overflow 后的自动裁剪兜底。压缩失败会使本轮 API 返回错误，但已生成的回答仍保存在历史中。

## 会话 API

当前是本地单用户服务，按会话 ID 隔离对话、待办和日志。写操作互斥只作用于一个进程，因此运行命令使用 `--workers 1`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/sessions` | 列出会话 |
| POST | `/sessions` | 新建，接受 `{}` 或 `{"title":"新会话"}` |
| GET | `/sessions/{session_id}` | 读取会话信息 |
| GET | `/sessions/{session_id}/messages` | 读取完整原始消息及当前状态 |
| GET | `/sessions/{session_id}/status` | 读取运行阶段、窗口用量和压缩状态 |
| POST | `/sessions/{session_id}/messages` | 发送 `{"content":"用户原文"}`，继续对话 |
| PATCH | `/sessions/{session_id}` | 以 `{"title":"新名称"}` 重命名 |
| DELETE | `/sessions/{session_id}` | 删除该会话及其相关数据 |

新建后保存返回的 `session_id`，读取历史和继续聊天均使用同一个 ID。不存在的 ID 返回 404，不隐式创建会话。新建、读取、重命名、删除不需要模型密钥。

同一会话运行时再次发送、重命名、删除返回 409，读取历史和其他会话不受此限制。接口错误包括：404 会话不存在、409 忙/未完成调用/步数上限、422 输入无效、503 模型未配置、502 运行失败。

前端在错误后重新读取已持久化历史，不自动重发。工具执行中断后若缺少结果，会阻止继续调用模型，避免盲目重复执行有副作用的操作；当前没有专门的 HTTP 恢复接口，需通过代码核实并补齐结果。

## SQLite 与空数据库样板

默认运行数据库是项目根目录的 `agent_logs.sqlite3`，保存会话、待办和调用日志。文件不存在时，代码会自动创建：会话相关表由 `ContextStore` 初始化，`calls` 在首次记录调用时创建。

仓库提供 [examples/agent_logs.empty.sqlite3](examples/agent_logs.empty.sqlite3)，包含当前全部应用表结构和索引，**所有应用表均为 0 行**，不含聊天、待办、模型响应或调用日志。全新检出且尚无运行数据库时，可选执行：

```sh
cp examples/agent_logs.empty.sqlite3 agent_logs.sqlite3
```

已有运行数据库时不要覆盖。应用只写根目录运行库，不修改 `examples/` 下的样板。根目录运行库及 SQLite 临时文件被 Git 忽略；交付的是空样板，不是本地运行历史。

| 表 | 内容 |
| --- | --- |
| `session_contexts` | 会话信息、当前摘要、保留历史、任务/环境/业务状态和最近用量 |
| `context_messages` | 按会话和顺序保存的全部原始消息 |
| `context_snapshots` | 压缩前状态、请求、状态和错误 |
| `calls` | 每次模型/工具调用的请求、响应、时间、耗时、状态及异常 |
| `todo_tool` | 自增 `id`、`session_id`、`content`、`stauts` |

日志区分普通模型调用与压缩调用，工具日志保留 `tool_call_id`。调用前写入 `running`，结束后更新为 `success` 或 `error`。代码不把 `.env` 和请求头写入日志；模型消息和工具内容会完整记录在本地数据库中。

## Python 调用

```python
from agent_loop import run_turn
from qwen_client import QwenClient
from session import Session

session = Session(messages=[{"role": "user", "content": "请计算 12 * 8"}])
client = QwenClient()
try:
    print(run_turn(session, client, max_steps=5))
    print("session_id:", session.session_id)

    restored = Session.restore(session.session_id)
    restored.add_message({"role": "user", "content": "把刚才的结果除以 3"})
    print(run_turn(restored, client))
finally:
    client.close()
```

可通过 `Session(log_path=...)` 使用另一个数据库；恢复时传入相同路径。测试或集成时，可向 `create_app()` 注入 `send_request`、`tools_dir`、`tool_handlers`、`prompt_manager` 和 `log_path`。显式传入工具映射会替代默认映射。

## 测试与开发记录

后端测试：

```sh
.venv/bin/python -m unittest discover -s tests -v
```

测试使用临时数据库、模拟模型响应或 HTTP MockTransport，不消耗真实模型额度。覆盖循环与次数限制、参数错误及工具失败、会话恢复和隔离、压缩边界/失败/快照、调用日志、计算、搜索和待办。

前端构建与浏览器测试：

```sh
npm run build
npm run test:ui
```

浏览器测试默认使用本机安装的 Google Chrome，启动临时后端和模拟 Qwen，端口为 8765 / 8766。覆盖发送、会话切换、刷新恢复、迟到响应、工具详情、错误处理、重命名删除、中文输入法、窄屏和压缩状态。请保持这些测试端口空闲。

实现提示词摘要、问题处理和验证方式见 [AI 开发记录](docs/ai-development-notes.md)。
