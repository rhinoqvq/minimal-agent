# Qwen3.8-Flash：Chat Completions 字段参考

核对日期：2026-11-06（Asia/Shanghai）。范围：`model=qwen3.8-flash`，`POST /compatible-mode/v1/chat/completions`。这是官方公开文档的字段整理，尚未用真实 API key 验证。服务地域、账户权限和文档版本可能影响可用性；明确不适用或尚未确认的字段单列在后面。

字段路径中的 `[]` 表示数组元素。表中的“默认”指省略该字段时的文档行为；“未明确”不等于 `null`。可选字段及细分返回字段可能缺失，不能要求每次响应返回全部字段。JSON Schema 中的业务字段名由我们定义，不属于固定 API 字段。

此处只记录协议，不实现 prompt 管理。

本项目当前约定：保留 `enable_thinking`，默认发送 `true`；不发送 `reasoning_effort`、`thinking_budget`、`max_completion_tokens` 或 `max_tokens`。推理力度、思考及回答长度均使用服务端默认行为。下表仍保留这些字段的协议说明，供查阅，不代表客户端需要实现它们。

不发送 `parallel_tool_calls`，不增加并发配置。后续工具执行采用顺序执行；若一次返回多个 `tool_calls`，依次执行并分别回填结果。

最小实现不发送 `stream`，使用默认的非流式响应，一次接收完整 JSON。下文流式字段仅作协议参考，暂不实现。

请求体的数据来源：`prompt_manager.py` 每次从 `prompt_store/system.md`、场景 MD、会话原始历史和上下文数据组装 `messages`，同时遍历 `tools/` 的 JSON 生成工具说明和 `tools` 字段。`agent_loop.py` 每次调用前重新组装，直到 `finish_reason=stop`；正常结束后按最后一次 Qwen usage 的输入与输出之和检查窗口占比，达到 80% 时额外压缩一次，再返回最终 `content`。原始对话与压缩快照由 `context_store.py` 存入 SQLite；`qwen_client.py` 通过 SDK 发出请求。本文仍是协议参考，未用真实模型响应验证。

## 请求地址、请求头与本地配置

中国站示例：`https://maas.qianwenaiapi.com/compatible-mode/v1/chat/completions`。API key 必须与调用平台和地域匹配。项目的 `.env` 暂用中国站 Base URL。参见[连接说明](https://platform.qianwenai.com/docs/api-reference/toolkitframework/openai-compatible/overview)。

| 位置 / 字段 | 类型 | 说明 |
| --- | --- | --- |
| 请求头 `Authorization` | string | 必填；`Bearer <API key>`。 |
| 请求头 `Content-Type` | string | 发送 JSON 时为 `application/json`。 |
| 请求头 `X-DashScope-OssResourceResolve` | string | 使用平台上传接口产生的临时 OSS 资源 URL 时设为 `enable`，普通文本请求不需要；参见[临时资源说明](https://platform.qianwenai.com/docs/api-reference/more/upload-file-get-temporary-url)。 |
| 本地 `QWEN_API_KEY` | string | 从 `.env` 读取，填入鉴权请求头，不放进 JSON 请求体。 |
| 本地 `QWEN_BASE_URL` | string | 本项目的地址配置；不放进 JSON 请求体。 |
| SDK `extra_body` | object | SDK 扩展参数容器。直接用 HTTP 时，把其中字段放在 JSON 顶层，不再包一层 `extra_body`。 |
| SDK `timeout` / `base_url` / `api_key` | 本地选项 | 属于客户端配置，不是本接口的 JSON 字段。 |

## 请求体：基础、生成与采样

下表整理[详细 API 参数](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)。不传可选项时由服务端取默认值。

| 字段 | 类型 | 必填 / 默认 | 用途与限制 |
| --- | --- | --- | --- |
| `model` | string | 必填 | 本项目固定为 `qwen3.8-flash`。 |
| `messages` | array | 必填 | 按顺序提交对话、模型工具调用及工具结果。 |
| `stream` | boolean | `false` | `false` 一次返回 JSON；`true` 返回 SSE 分块。 |
| `stream_options` | object | 可选 | 仅对流式请求生效。 |
| `stream_options.include_usage` | boolean | `false` | 要求在流的末尾返回用量。 |
| `max_completion_tokens` | integer | 可选；本项目不传 | 限制本次总输出，包含思考；到达上限时 `finish_reason=length`。 |
| `max_tokens` | integer | 旧字段，待废弃；本项目不传 | 对 Flash 限制回答部分。 |
| `temperature` | number | 见默认值差异 | 随机性；范围 `[0,2)`，思考模式低于 `0.6` 会被调整到 `0.6`。 |
| `top_p` | number | 思考 `0.95`；非思考 `0.8` | 按累计概率保留候选，范围 `(0,1]`。 |
| `top_k` | integer / null | `20` | 候选 token 个数；非负整数。`null` 或大于 `100` 时不使用 top-k。 |
| `repetition_penalty` | number | `1.0` | 抑制重复序列；大于 `0`，`1` 表示不施加此惩罚。 |
| `presence_penalty` | number | 思考 `0`；非思考 `1.5` | 对已出现 token 施加惩罚，范围 `[-2,2]`。 |
| `seed` | integer | 未明确 | 随机种子，范围 `[0,2147483647]`；提高可复现性，不保证逐字相同。 |
| `stop` | string / array | 可选 | 停止字符串或 token 序列；数组内不能混用字符串和 token ID。 |
| `n` | integer | `1` | 候选回答数量；没有 Qwen3.8-Flash 支持大于 `1` 的明确说明，工具调用使用 `1`。 |

## 请求体：思考

依据[模型专页](https://docs.qwencloud.com/developer-guides/getting-started/latest-model)及[思考指南](https://docs.qwencloud.com/developer-guides/text-generation/thinking)。互斥规则见[详细 API](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)。

| 字段 | 类型 | 默认 | 用途与限制 |
| --- | --- | --- | --- |
| `enable_thinking` | boolean | `true` | 控制是否生成思考内容；关闭后直接回答。 |
| `reasoning_effort` | string | `xhigh` | `low` / `medium` / `xhigh`；模型专页说明 `high`、`max` 映射为 `xhigh`。 |
| `thinking_budget` | integer | 文档有差异，见后文 | 思考 token 预算；与 `reasoning_effort` 二选一。 |
| `preserve_thinking` | boolean | 模型专页：`true` | 让模型使用消息历史中回传的 `reasoning_content`；增加相应输入用量。 |

工具调用后的历史 assistant 消息应原样保留 `reasoning_content`；不要把它合并进 `content`。这个字段是返回数据，harness 负责保存与回传，不需要自己生成。

## 请求体：工具定义与选择

字段结构与回填规则见[Function Calling](https://docs.qwencloud.com/developer-guides/tool-calling/function-calling)；当前 Qwen 的 `required` 限制见[详细 API](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)。

| 字段 | 类型 | 必填 / 默认 | 用途与限制 |
| --- | --- | --- | --- |
| `tools` | array | 可选 | 向模型提供可以请求执行的工具。 |
| `tools[].type` | string | 必填：`function` | 工具类型。 |
| `tools[].function` | object | 必填 | 工具定义。 |
| `tools[].function.name` | string | 必填 | 函数名称，与本地注册名对应；使用字母、数字、下划线或连字符。 |
| `tools[].function.description` | string | 按 API 表为必填 | 描述用途和适用条件。 |
| `tools[].function.parameters` | object | 默认 `{}` | 工具输入的 JSON Schema；里面的业务属性由我们定义。 |
| `tool_choice` | string / object | `auto` | `auto` 由模型选择，`none` 禁用；非思考模式还可传指定函数对象。 |
| `tool_choice.type` | string | 指定函数时：`function` | 对象写法的工具类型。 |
| `tool_choice.function` | object | 指定函数时必填 | 指定工具的容器。 |
| `tool_choice.function.name` | string | 指定函数时必填 | 必须是 `tools` 中已定义的名称。 |
| `parallel_tool_calls` | boolean | `true` | 允许一次返回多个工具调用；本项目不传此字段，本地工具按顺序执行。 |
| `tool_stream` | boolean | `false` | 只对 `stream=true` 有意义；控制含 object/array 等复杂参数的工具调用是否增量输出。 |

思考模式只能用 `tool_choice=auto/none`。`required` 不能作为 Qwen 的强制调用保证：思考模式不支持，非思考模式也不保证一定调用。`function.arguments` 始终先按字符串接收，完整后解析、校验再执行。

## 请求体：结构化回答

Qwen3.8-Flash 支持 JSON Object 和 JSON Schema。多模态输入的 `json_schema` 会退化为 `json_object`，不能依赖 Schema 约束。参见[结构化输出](https://docs.qwencloud.com/developer-guides/text-generation/structured-output)。

| 字段 | 类型 | 必填 / 默认 | 用途 |
| --- | --- | --- | --- |
| `response_format` | object | 默认 `{"type":"text"}` | 约束正文的格式，不改变外层 `choices` 响应结构。 |
| `response_format.type` | string | 对象内必填 | `text` / `json_object` / `json_schema`。 |
| `response_format.json_schema` | object | `type=json_schema` 时必填 | Schema 配置。 |
| `response_format.json_schema.name` | string | 必填 | Schema 名称，最多 64 字符。 |
| `response_format.json_schema.description` | string | 可选 | Schema 的语义说明。 |
| `response_format.json_schema.schema` | object | 必填 | 描述实际回答 JSON 的结构。 |
| `response_format.json_schema.strict` | boolean | `false` | 设为 `true` 启用严格结构约束。 |

`json_object` 要求消息中明确包含 JSON 指令。无论哪种模式，`message.content` 仍是字符串，需再做 JSON 解析；截断的输出不能直接交给下游。

## 请求体：内置搜索

这组字段控制服务端搜索。模型要求本地执行工具则走前面的 `tools`。支持范围见[联网搜索指南](https://platform.qianwenai.com/docs/developer-guides/tool-calling/web-search)。

| 字段 | 类型 | 默认 | 用途 / 范围 |
| --- | --- | --- | --- |
| `enable_search` | boolean | `false` | 启用服务端联网搜索。 |
| `search_options` | object | 可选 | 配置搜索行为。 |
| `search_options.forced_search` | boolean | `false` | 强制本轮搜索；否则由模型判断。 |
| `search_options.search_strategy` | string | `turbo` | 本模型使用 `turbo` 或 `max`；Chat 接口不支持 `agent`。 |
| `search_options.enable_search_extension` | boolean | `false` | 启用天气、股票等垂直数据查询。 |
| `include_tool_usage` | boolean | `false` | 实际触发搜索时，在 `usage.x_tools` 返回调用统计。 |

`freshness`、`assigned_site_list`、`intention_options.prompt_intervene` 的支持名单未明确包含 `qwen3.8-flash`；见后文“待验证字段”，不将它们当作本模型已确认支持。

## messages：每条消息的字段

根据[消息定义](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)、[思考回传](https://docs.qwencloud.com/developer-guides/text-generation/thinking)与[显式缓存](https://help.aliyun.com/zh/model-studio/context-cache)整理。通常使用字符串 `content`；多模态或缓存使用内容块数组。

| 字段 | 类型 | 使用条件 / 含义 |
| --- | --- | --- |
| `messages[].role` | string | 必填；`system`、`user`、`assistant`、`tool`。 |
| `messages[].content` | string / array / null | 消息内容。user 为文本或多模态块；system 通常为文本；assistant 有工具调用时可空；普通 tool 结果须序列化为字符串。缓存指南还定义了内容块写法。 |
| `messages[].reasoning_content` | string | assistant 历史思考；模型返回后保存并回传。 |
| `messages[].tool_calls` | array | assistant 请求的一个或多个工具调用。 |
| `messages[].tool_calls[].id` | string | 工具调用的配对 ID。 |
| `messages[].tool_calls[].type` | string | 当前为 `function`。 |
| `messages[].tool_calls[].function` | object | 所请求的函数与参数。 |
| `messages[].tool_calls[].function.name` | string | 工具名称。 |
| `messages[].tool_calls[].function.arguments` | string | JSON 编码的参数字符串，不是 JSON 对象。 |
| `messages[].tool_calls[].index` | integer | 工具序号，尤其用于流式合并；响应给出时保留，勿与 id 混用。 |
| `messages[].tool_call_id` | string | 普通 tool 消息必填，与此前 assistant 的工具调用 id 对应。 |

一次返回多个调用时，每个调用都需要对应的工具结果。完整 assistant 工具调用消息先入历史，随后追加 tool 消息，再请求模型。`messages[].partial` 在通用协议存在，但本模型支持未确认，见后文。

## content 数组：文本、图片、视频与缓存

数组元素记为 `messages[].content[]`。图像和视频格式见[视觉输入指南](https://docs.qwencloud.com/developer-guides/multimodal/vision)；像素上限见[API 多模态字段](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)；缓存扩展见[Context Cache](https://help.aliyun.com/zh/model-studio/context-cache)。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `messages[].content[].type` | string | 当前范围使用 `text`、`image_url`、`video_url`、`video`。 |
| `messages[].content[].text` | string | `type=text` 时的文本。 |
| `messages[].content[].image_url` | object | 图片信息，`type=image_url` 时使用。 |
| `messages[].content[].image_url.url` | string | 图片 URL 或 Base64 Data URL。 |
| `messages[].content[].video_url` | object | 视频文件信息，`type=video_url` 时使用。 |
| `messages[].content[].video_url.url` | string | 视频 URL 或支持的 Base64 Data URL。 |
| `messages[].content[].video` | array of string | `type=video` 时，按时间顺序给出各帧图片。 |
| `messages[].content[].fps` | number | 视频抽帧频率；范围 `[0.1,10]`，默认 `2`。帧列表中用于描述帧间时间。放在内容块上，与 `video_url` / `video` 同级。 |
| `messages[].content[].min_pixels` | integer | 图像或视频单帧的像素下限。 |
| `messages[].content[].max_pixels` | integer | 图像或视频单帧的像素上限。 |
| `messages[].content[].total_pixels` | integer | 视频所有帧合计的像素预算；本模型文档默认及最大值为 `819200000`。 |
| 顶层 `vl_high_resolution_images` | boolean | 默认 `false`；图片高分辨率开关。开启后图片像素上限固定为 `16777216`，忽略图片 `max_pixels`。 |
| `messages[].content[].cache_control` | object | 将该内容块标记为显式缓存边界。 |
| `messages[].content[].cache_control.type` | string | 当前为 `ephemeral`。 |
| `messages[].content[].tool_call_id` | string | 缓存指南中的合并 tool 内容块扩展：每个结果块标记调用 id。普通路径优先使用独立 tool 消息。 |

图片 `max_pixels` 默认 `2621440`、最大 `16777216`；视频单帧默认 `655360`、最大 `2048000`。缓存有效期为 5 分钟，最小前缀 1024 tokens，单次最多 4 个标记。显式缓存要求将对应 `content` 改为数组；没有另外的顶层 `cache_control`。当前文档未给出可配置 `ttl` 字段。

## 普通返回：顶层和 choices

以下是[普通返回对象](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)的字段清单。正文、思考、工具调用分开接收，不把整个响应简化成一个字符串。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | string | 本次响应标识。 |
| `object` | string | 非流式为 `chat.completion`。 |
| `created` | integer | 请求创建时间，Unix 秒。 |
| `model` | string | 响应所用模型标识。 |
| `choices` | array | 生成结果；不要把数组当作消息对象。 |
| `choices[].index` | integer | 候选回答序号。 |
| `choices[].finish_reason` | string | `stop` 正常结束；`length` 达到长度限制；`tool_calls` 转入工具执行。 |
| `choices[].message` | object | 完整 assistant 消息。 |
| `choices[].message.role` | string | `assistant`。 |
| `choices[].message.content` | string / null | 最终正文；工具调用时可能为空。 |
| `choices[].message.reasoning_content` | string / null | 思考内容；关闭思考时可能缺失。 |
| `choices[].message.tool_calls` | array / null | 工具调用列表，结构见下一表。 |
| `usage` | object | 本次用量，子字段见后文。 |
| `service_tier` | null | 通用返回占位，当前文档固定为空。 |
| `system_fingerprint` | null | 通用返回占位，当前文档固定为空。 |
| `choices[].message.refusal` | null | 当前文档固定为空，不作为唯一拒答判据。 |
| `choices[].message.audio` | null | 此模型不输出音频，通用响应占位。 |
| `choices[].message.function_call` | null | 旧字段，已废弃；使用 `tool_calls`。 |
| `choices[].logprobs` | object / null | 协议定义的 token 概率对象；Flash 的非空支持未确认，通常不依赖。 |

## 返回的工具调用

依据[Function Calling](https://docs.qwencloud.com/developer-guides/tool-calling/function-calling)。以下路径位于 `choices[].message.tool_calls[]`。

| 相对字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | string | 执行后通过 `tool_call_id` 回填的关联 ID。 |
| `type` | string | `function`。 |
| `function` | object | 工具调用内容。 |
| `function.name` | string | 应在本地工具注册表中查找的函数。 |
| `function.arguments` | string | 完整或待拼接的 JSON 参数文本。 |
| `index` | integer | 同一回答中的工具序号；流式时必须按它合并。 |

`tool_calls` 可以有多个元素。参数解析错误、未知工具和工具执行失败应作为 harness 的明确分支，不能把 `content=null` 当作调用失败。

## usage：用量与缓存

通用 token 字段见[返回参数](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)，缓存统计见[Context Cache](https://help.aliyun.com/zh/model-studio/context-cache)，搜索统计见[联网搜索](https://platform.qianwenai.com/docs/developer-guides/tool-calling/web-search)。以下路径均相对于 `usage`；细分对象可能缺失或为 `null`。

| 相对字段 | 类型 | 含义 |
| --- | --- | --- |
| `prompt_tokens` | integer | 输入 token 总数。 |
| `completion_tokens` | integer | 输出 token 总数，包含思考。 |
| `total_tokens` | integer | 输入与输出 token 之和。 |
| `prompt_tokens_details` | object / null | 输入细分。 |
| `prompt_tokens_details.text_tokens` | integer | 输入文本 token。 |
| `prompt_tokens_details.image_tokens` | integer | 输入图片 token。 |
| `prompt_tokens_details.video_tokens` | integer | 输入视频 token。 |
| `prompt_tokens_details.audio_tokens` | integer / null | 通用协议的音频计数；本模型不依赖该字段。 |
| `prompt_tokens_details.cached_tokens` | integer | 命中缓存的输入 token，是输入总数的子集。 |
| `prompt_tokens_details.cache_creation` | object | 显式缓存创建细分。 |
| `prompt_tokens_details.cache_creation.ephemeral_5m_input_tokens` | integer | 5 分钟缓存创建 token。 |
| `prompt_tokens_details.cache_creation_input_tokens` | integer | 显式缓存创建 token。 |
| `prompt_tokens_details.cache_type` | string | 显式缓存文档值为 `ephemeral`；不要把未知值当解析失败。 |
| `completion_tokens_details` | object / null | 输出细分。 |
| `completion_tokens_details.text_tokens` | integer | 输出文本 token，包含思考。 |
| `completion_tokens_details.reasoning_tokens` | integer / null | 其中属于思考的 token；是 text_tokens 的子集。 |
| `completion_tokens_details.audio_tokens` | integer / null | 通用协议的音频计数；本模型不依赖该字段。 |
| `x_tools` | object | 启用统计且实际触发搜索时出现。 |
| `x_tools.search` | object | 搜索统计。 |
| `x_tools.search.count` | integer | 触发搜索的次数；不是成功次数或账单依据。 |
| `x_tools.search.strategy` | string | 实际搜索策略，例如 `turbo`、`max`。 |

这些细分不能全部相加：例如 cached_tokens 已在 prompt_tokens 内，reasoning_tokens 已在 text_tokens 内。若同时返回两个缓存创建字段，也不要重复累加。

## 流式返回：SSE 与 delta

依据[流式指南](https://docs.qwencloud.com/developer-guides/run-and-scale/streaming)及[完整 chunk 示例](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)。HTTP 成功后读取 SSE 事件，JSON chunk 位于 `data:` 中；`data: [DONE]` 是结束标记，不是 JSON 字段。

| 字段 | 类型 | 与非流式的关系 |
| --- | --- | --- |
| `id` / `created` / `model` | 同普通响应 | 标识当前请求；保留用于诊断。 |
| `object` | string | 变为 `chat.completion.chunk`。 |
| `choices` | array | 可能为空，特别是最后的 usage chunk。 |
| `choices[].index` | integer | 候选回答索引。 |
| `choices[].delta` | object | 新增内容，替代完整的 `message`。 |
| `choices[].delta.role` | string / null | 角色，通常只在开头出现。 |
| `choices[].delta.content` | string / null | 正文增量，按顺序拼接。 |
| `choices[].delta.reasoning_content` | string / null | 思考增量，单独拼接。 |
| `choices[].delta.tool_calls` | array / null | 工具调用增量。 |
| `choices[].delta.tool_calls[].index` | integer | 用于归并同一个工具调用。 |
| `choices[].delta.tool_calls[].id` | string / null | 调用 ID，可能只在开始时给出。 |
| `choices[].delta.tool_calls[].type` | string / null | 工具类型，可能只在开始时给出。 |
| `choices[].delta.tool_calls[].function` | object | 函数信息的增量对象。 |
| `choices[].delta.tool_calls[].function.name` | string / null | 名称信息；后续分块不一定再次提供。 |
| `choices[].delta.tool_calls[].function.arguments` | string / null | 参数字符串片段；收齐后才能做 JSON 解析。 |
| `choices[].finish_reason` | string / null | 生成过程中为 null，结束时给出停止原因。 |
| `choices[].logprobs` | object / null | 通用概率占位；不假定 Flash 返回非空。 |
| `choices[].delta.refusal` / `choices[].delta.function_call` | null | 通用占位 / 旧字段。 |
| `usage` | object / null | 开启 include_usage 后在末尾读取；结构同普通响应。 |
| `service_tier` / `system_fingerprint` | null | 通用占位。 |

harness 应区分：某个 choice 结束、收到 usage、整个 SSE 收到 `[DONE]`。网络断流不能直接当成成功完成；工具参数未收齐时不能执行工具。

## 错误响应

[官方错误响应示例](https://docs.bailian.console.aliyun.com/en/model-studio/compatibility-of-openai-with-dashscope)使用下面的对象；错误响应不按成功响应的 `choices` 读取。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `error` | object | 错误详情。 |
| `error.message` | string | 供人阅读的说明。 |
| `error.type` | string | 错误类别。 |
| `error.code` | string / null | 具体错误码。 |
| `error.param` | string / null | 相关参数，可能为空。 |

harness 还应记录 HTTP 状态和服务端返回的请求标识（若有）；它们不等同于每个 JSON 错误体都必有的字段。4xx、5xx、网络错误、流中断需要分别处理。

## Schema 对象中的关键字

`tools[].function.parameters` 和 `response_format.json_schema.schema` 中的业务属性名称由应用定义，因此没有固定的“全部业务字段”。常用关键字是 `type`（类型）、`properties`（属性）、`required`（必填列表）、`items`（数组元素）、`enum`（枚举）和 `description`（说明）。严格输出的支持子集与普通 JSON Schema 不完全相同，见[官方关键字清单](https://docs.qwencloud.com/developer-guides/text-generation/structured-output#constrain-output-precisely-with-json-schema)。

这里不将 JSON Schema 的所有标准关键字等同于模型 API 支持的工具参数约束；实现时对具体工具的 Schema 单独验证。

## 通用文档中出现、但本模型或本接口不能直接采用的字段

下表解释这些字段的用途，同时区分“明确不适用”和“支持未确认”，避免将通用接口字段全部当成 Qwen3.8-Flash 能力。

| 字段 / 取值 | 用途 | 本项目的处理依据 |
| --- | --- | --- |
| `modalities`、`audio`、`audio.voice`、`audio.format` | 选择输出模态、音色和音频格式 | [API](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)限定为相关 Omni 模型；不适用此 Flash。 |
| `messages[].content[].input_audio` 及其 `data` / `format` | 输入音频 | [模型专页](https://docs.qwencloud.com/developer-guides/getting-started/latest-model)列出文本、图片、视频输入；不将此模型当 Omni 使用。 |
| `thinking`、`thinking.type` | 通过 adaptive/disabled 控制思考 | [API](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)中属于 MiniMax；Qwen 使用 enable_thinking。 |
| `reasoning_split` | 选择思考内容的返回位置 | [API](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)中属于 MiniMax。 |
| `clear_thinking` | 是否排除历史思考 | [API](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)中属于 GLM。 |
| `skill`、`skill.type`、`skill.mode`、`skill.template_id` | PPT 生成技能配置 | [API](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)仅为 qwen-doc-turbo 定义。 |
| `enable_code_interpreter` | 服务端运行代码 | [专页](https://docs.qwencloud.com/developer-guides/tool-calling/code-interpreter)将 qwen3.8-flash 的支持限定在 Responses；不列为本模型 Chat 字段。 |
| `tool_choice=required` | 强制调用任意工具 | Qwen 思考模式不支持，非思考模式也不保证；见[API](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)。 |
| 思考模式下指定 `tool_choice.function.name` | 强制调用指定函数 | [Function Calling](https://docs.qwencloud.com/developer-guides/tool-calling/function-calling)只允许 auto/none。 |
| `search_options.search_strategy=agent` | 服务端多轮自主检索 | [搜索指南](https://platform.qianwenai.com/docs/developer-guides/tool-calling/web-search)明确排除 Qwen3.8 的 Chat/DashScope 路径；需用 Responses。 |
| `search_options.enable_source` | 返回搜索来源 | [搜索指南](https://platform.qianwenai.com/docs/developer-guides/tool-calling/web-search)限定 DashScope。 |
| `search_options.enable_citation` | 插入引用编号 | 同上，限定 DashScope。 |
| `search_options.citation_format` | 引用编号样式 | 同上，限定 DashScope。 |
| `search_options.prepend_search_result` | 先流出搜索来源 | 同上，限定 DashScope 流式。 |
| `search_options.freshness` | 限制来源新鲜度：7/30/180/365 天 | [搜索指南](https://platform.qianwenai.com/docs/developer-guides/tool-calling/web-search)未明确列入 qwen3.8-flash；待验证。 |
| `search_options.assigned_site_list` | 限定最多 25 个站点 | 同上，未明确支持本模型；待验证。 |
| `search_options.intention_options` | 检索意图配置对象 | 同上，未明确支持本模型；待验证。 |
| `search_options.intention_options.prompt_intervene` | 用自然语言限定检索方向 | 同上，未明确支持本模型；待验证。 |
| `enable_text_image_mixed` | 回答中嵌入图片 | [支持名单](https://platform.qianwenai.com/docs/developer-guides/tool-calling/web-search)未明确包含 qwen3.8-flash。 |
| `logprobs` | 返回输出 token 的对数概率，默认 false | [API](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)的支持名单未包含 qwen3.8-flash；待验证。 |
| `top_logprobs` | 每个位置的候选 token 数，0–5 | 依赖 logprobs；本模型支持未确认。 |
| `messages[].partial` | 续写最后一条 assistant 内容，默认 false | [前缀续写支持名单](https://help.aliyun.com/zh/model-studio/partial-mode)未列 Qwen3.8-Flash；待验证。 |
| `messages[].content[].max_frames` | 限制抽取帧数 | [视觉指南](https://docs.qwencloud.com/developer-guides/multimodal/vision)注明只对 DashScope SDK 有效。 |
| `frequency_penalty` | 按重复次数惩罚 token | [兼容说明](https://platform.qianwenai.com/docs/api-reference/toolkitframework/openai-compatible/overview)列为忽略字段；不要用它代替 repetition_penalty。 |
| `logit_bias` | 修改指定 token 的概率 | 兼容说明列为忽略字段。 |
| `metadata` | 附带业务元数据 | 兼容说明列为忽略字段，改在本地保存。 |
| `prediction` | 提供预测输出 | 兼容说明列为忽略字段。 |
| `prompt_cache_key` | 指定缓存键 | 兼容说明列为忽略字段；使用本平台缓存机制。 |
| `service_tier` | 选择服务档位 | 兼容说明列为忽略请求字段。 |
| `store` | 要求服务端保存结果 | 兼容说明列为忽略字段；本地自行保存对话。 |
| `verbosity` | 设置回答详细度 | 兼容说明列为忽略字段。 |
| `web_search_options` | 其他接口的搜索配置 | [兼容说明](https://platform.qianwenai.com/docs/api-reference/toolkitframework/openai-compatible/overview)要求用 enable_search/search_options。 |
| `input`、`previous_response_id` | Responses 输入和历史关联 | 属于另一接口，不是本次 Chat 请求体字段。 |

## 可见但不应依赖的概率返回结构

为完整覆盖[通用返回 schema](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)，列出 `choices[].logprobs` 的子字段。它们的存在不等于本模型支持生成非空概率结果。

| 相对字段 | 类型 | 用途 |
| --- | --- | --- |
| `content` | array | 按输出 token 排列的概率记录。 |
| `content[].token` | string | 该 token 的文本。 |
| `content[].bytes` | integer[] | 该 token 的 UTF-8 字节。 |
| `content[].logprob` | number / null | 该 token 的对数概率；null 可表示极低概率。 |
| `content[].top_logprobs` | array | 当前位置的候选 token 概率。 |
| `content[].top_logprobs[].token` | string | 候选 token 文本。 |
| `content[].top_logprobs[].bytes` | integer[] | 候选 token 的 UTF-8 字节。 |
| `content[].top_logprobs[].logprob` | number / null | 候选 token 的对数概率。 |

## 文档差异与实现取舍

以下是本次核对实际发现的差异，不把它们包装成已实测的统一行为。

| 项目 | 官方描述差异 | 本项目建议 |
| --- | --- | --- |
| temperature 默认值 | [模型页](https://docs.qwencloud.com/developer-guides/getting-started/latest-model)笼统写思考模式 0.6；[详细 API](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)区分文本 1.0、视觉 0.6、非思考 0.7。 | 需要稳定配置时显式指定；默认行为待实际端点验证。 |
| preserve_thinking 默认值 | [详细 API](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)通用标签为 false；[模型页](https://docs.qwencloud.com/developer-guides/getting-started/latest-model)明确 Flash 默认 true。 | 按模型页记录，调用时显式 true，并回传 reasoning_content。 |
| thinking_budget 默认值 | [详细 API](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)通用描述为模型上限，但 reasoning_effort 小节又写两者省略时为 131072。 | 本项目不传 budget 或 effort，使用服务端默认；不把最大思考长度当成默认值。 |
| reasoning_effort 与 max_completion_tokens | [兼容概览](https://platform.qianwenai.com/docs/api-reference/toolkitframework/openai-compatible/overview)仍将两者列为忽略字段，但[详细 API](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)及模型专页明确给出 Qwen3.8 支持规则。 | 保留协议差异说明；本项目不传这两个字段。 |
| cache_type | [API](https://platform.qianwenai.com/docs/api-reference/chat/openai-chat)描述显式值 ephemeral；[缓存相关示例](https://help.aliyun.com/zh/model-studio/partial-mode)也出现 implicit。 | 保留字符串原值，不建立只有 ephemeral 的硬性校验。 |

详细 API 的 effort 映射为：low → 4096，medium → 16384，xhigh → 262144；与 thinking_budget 同传会报错。模型专页的上下文为 1000000 tokens、最大思考长度 262144、最大回答长度 131072；这些是容量说明，不代表应把所有请求限制都设置成最大值。

当前配置仅包含空 API key 与 Base URL。此文档不意味着客户端、工具执行循环或流式解析代码已经实现。

