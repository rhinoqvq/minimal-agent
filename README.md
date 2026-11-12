# Minimal Agent

一个基于 Qwen Chat Completions 的最小 Agent，支持多轮对话、工具调用、独立会话、基础上下文压缩和本地日志。设计重点是把模型交互、Prompt 组装、工具执行和会话存储拆开，保持实现简单、运行过程可追查。

后端使用 Python + FastAPI，前端使用 React + Vite，数据保存在 SQLite。当前模型配置为 `qwen3.8-flash`，开启思考模式；不传流式、推理力度或输出长度参数。

## 运行方式

需要 Python 3.10+、Node.js 20.19+ 或 22.12+。在项目根目录执行（以下为 macOS / Linux 命令）：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci
cp .env.example .env
```

在本地 `.env` 填写 `QWEN_API_KEY`，并将 `QWEN_BASE_URL` 设置为与密钥对应的服务地址。环境变量会覆盖 `.env` 的同名配置。仓库只提供不含真实密钥的 `.env.example`，真实 `.env` 被 Git 忽略。

分别打开两个终端：

```sh
# 后端 API
.venv/bin/uvicorn api:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

```sh
# 前端：先构建，再启动静态服务
npm start
```

打开 [聊天页面](http://127.0.0.1:8001)，接口文档在 [后端 /docs](http://127.0.0.1:8000/docs)。修改前端时可用 `npm run dev`，不要与 `npm start` 同时占用 8001。当前会话互斥只适用于单进程，因此后端使用 `--workers 1`。

## 系统设计

```text
用户输入 + session_id
    → API 恢复会话并保存输入
    → Prompt 组装
    → 请求 LLM
        ├─ tool_calls → 执行工具 → 回填结果 → 重新组装并请求 LLM
        └─ stop → 保存最终回答 → 检查用量/必要时压缩 → 返回前端
```

| 模块 | 职责 |
| --- | --- |
| `qwen_client.py` | 从环境配置读取密钥，发送非流式模型请求 |
| `agent_loop.py` | 解析返回、分派工具、回填结果和限制循环步数 |
| `session.py` / `api.py` | 会话创建、恢复、历史读取和 HTTP 入口 |
| `prompt_manager.py` / `prompt_store/` | 模板读取、变量替换、上下文组装和压缩 |
| `tools/` / `tool_functions.py` / `todo_tool.py` | 工具 Schema、函数绑定和具体实现 |
| `context_store.py` / `agent_log.py` | SQLite 上下文存档、压缩快照和调用日志 |
| `frontend/` | 会话列表、聊天界面、工具详情和状态展示 |

### 模型循环与异常

模型通过 `tool_calls` 请求工具，参数为 JSON 字符串。程序先保存 assistant 调用消息，依次执行工具，再把结果以 `role: "tool"` 和对应的 `tool_call_id` 追加到历史。下一次模型请求携带这些消息，保留 `reasoning_content` 等原有字段。

`finish_reason=stop` 时才把 `content` 当作最终正文。工具调用轮即使带正文也会继续执行；`length`、无效返回和模型请求失败会报错。工具参数或执行错误作为工具结果回传，由模型决定是否修正重试，同时记录失败日志。

每轮默认最多 **5 次普通模型请求**，每种工具最多尝试 **5 次**，失败也计数；新一轮重置计数。压缩是正常 loop 结束后的独立请求，不占这 5 步。用户累计聊天轮数不设固定上限。

### 工具

每次组装 Prompt 时递归读取 `tools/**/*.json`，检查基本结构和名称重复。同一份定义用于 Prompt 中的工具说明和请求体 `tools`，实际执行函数通过名称映射绑定。

| 工具 | 功能与实现 |
| --- | --- |
| `calculate` | 接收表达式字符串，支持十进制加减乘除、括号和整数幂；用 AST 白名单与 Decimal 计算，结果以字符串返回 |
| `search` | 调用 DDGS `text()`，固定 `backend="auto"`、`region="jp-ja"`、最多 10 条；返回标题、链接和摘要列表 |
| `todolist` | 当前会话内的待办增删改查，数据保存在 SQLite |

待办 `action` 为 `0=新增`、`1=列表`、`2=按 id 查询`、`3=修改`、`4=删除`。表字段为 `id、session_id、content、stauts`，状态为“待办 / 进行中 / 已完成”。`id` 自增，`session_id` 由后端绑定，模型不能指定其他会话。

字符串结果直接回填，字典或列表由 loop 转为 JSON 字符串后保存。工具错误、结果和次数限制复用同一条执行路径。详细参数见 [工具说明](tools/README.md)；只添加 JSON 声明不会自动生成执行函数。

### 会话与前端

前端新建会话后取得固定 `session_id`，切换会话时用它读取历史，发送时提交 `{"content":"用户原文"}`。后端恢复对应上下文继续运行，重命名不改变 ID，重启后仍可续聊。会话历史、摘要、待办和日志按 ID 隔离。

| 操作 | API |
| --- | --- |
| 列表 / 新建 | `GET /sessions` / `POST /sessions` |
| 信息 / 重命名 / 删除 | `GET` / `PATCH` / `DELETE /sessions/{session_id}` |
| 历史 / 发送消息 | `GET` / `POST /sessions/{session_id}/messages` |
| 运行及压缩状态 | `GET /sessions/{session_id}/status` |

同一会话运行中再次发送、重命名或删除返回 409；历史仍可读取，其他会话可独立运行。当前是本地单用户服务，没有多用户鉴权。

前后端均不做真实流式传输。前端等待完整回答后播放打字机动画，历史直接完整显示；工具详情可折叠查看。切换会话不会把旧响应显示到新会话。请求失败后重新读取已保存历史，不自动重复发送。输入框下方显示上下文用量和压缩状态，待办通过聊天操作。

### Prompt 与上下文压缩

通用规则独立放在 `system.md`，普通和压缩两个场景分别放在 `normal.md`、`compress.md`。文件位于 `prompt_store/`，每次请求重新读取并替换工具说明、摘要、任务状态、环境及业务数据等变量，不设置语言目录。

普通 loop 正常结束后，读取最后一次 Qwen 响应：

```text
used_tokens = usage.prompt_tokens + usage.completion_tokens
ratio = used_tokens / context_window
```

本地窗口配置默认为 `1_000_000`，达到 **80%** 时额外调用 LLM 生成基础摘要。压缩提示词是：“请压缩以下对话，保留你觉得重要的内容，只输出压缩后的摘要。”切换模型时应相应调整本地窗口配置。

压缩前保存快照，将旧摘要与可压缩历史交给模型；最新用户问题、最终回答、系统消息和未完成工具调用组保留原文。摘要正常完成、非空且更短时，才通过事务替换有效上下文；失败或压缩期间状态已更新时保留原数据。完整原始消息始终另行存档。

压缩后的准确用量等下一次普通响应更新，界面标记“待更新”，不会使用压缩请求的 token 数代替新上下文用量。目前不做请求前本地 token 估算或超限自动裁剪；压缩失败会返回错误，已生成的回答仍可从历史读取。

## Memory 的召回时机与放置方式

当前 memory 实现是**会话内的历史、摘要和持久化待办**，没有跨会话长期记忆、Embedding 或自动检索完整历史的模块。

**召回时机**：收到带 `session_id` 的新消息时，从 SQLite 恢复该会话的当前状态；每次模型请求前，将当前摘要和保留历史重新组装进请求。待办信息由模型按需调用 `todolist` 获取。完整原始存档供历史查看，压缩后不会全部重新塞回模型。

**放置方式**：

1. 首条 `system` 消息放通用规则、场景指导和额外系统规则。
2. 有摘要或状态数据时，在历史之前插入一条标记为 `<context_data>` 的 `user` 消息，包含历史摘要、任务状态、环境信息和业务数据。
3. 后面按原顺序追加保留的用户消息、模型输出和工具结果，保持工具调用 ID 的对应关系，支持普通追问和带工具的追问。
4. `todolist` 查询结果作为 `tool` 消息进入本轮上下文；待办记录不会随聊天压缩删除。

## 本地数据库与调用日志

默认运行库为根目录 `agent_logs.sqlite3`，不存在时由代码自动建表。它包含五张应用表：`session_contexts`（当前上下文）、`context_messages`（完整原始消息）、`context_snapshots`（压缩快照）、`calls`（模型/工具日志）、`todo_tool`（待办）。

每次模型或工具调用先记录请求和 `running` 状态，完成后更新响应、耗时、结果或异常；日志带会话 ID 和工具调用 ID，方便诊断。日志不记录 `.env` 或请求头，消息和工具内容保存在本地运行库。

仓库提供 [空数据库样板](examples/agent_logs.empty.sqlite3)，包含表结构和索引，应用表均无记录。全新检出且没有运行数据库时，可以复制使用；已有数据库不要覆盖：

```sh
cp examples/agent_logs.empty.sqlite3 agent_logs.sqlite3
```

运行时只写根目录数据库，该文件与 SQLite 临时文件被 Git 忽略，`examples/` 下的空样板不会被应用修改。

## 验证与开发记录

```sh
# 检查前端能否构建
npm run build
```

开发过程中使用模拟模型和临时数据库完成本地验证，检查循环、工具、会话隔离/恢复、压缩、日志与错误处理。测试代码保留在本地，当前仓库不包含测试目录或浏览器测试配置。

真实模型和搜索连接需要本地密钥及网络。可用“计算 `0.1 + 0.2`”“把刚才结果乘以 10”“新增待办并查询”，以及切换会话检查历史与待办隔离，进行人工体验。

AI Prompt 与问题解决过程见 [原始开发问答](conversation.md)，整理说明见 [AI 开发记录](docs/ai-development-notes.md)。
