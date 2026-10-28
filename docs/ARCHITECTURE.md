# Baton v0.2 架构

Baton 的目标是把设计文档 v0.1 的主干跑通：**Agent 只做编排，所有系统能力经能力网关统一成工具**；系统后端零改动接入，界面能力用一个 SDK 暴露，写操作必须预览并由用户确认。v0.2 在 v0.1 的基础上重做了工作台，并补齐常规 Agent 工作台需要的对话保存、流式输出、停止、重新生成、断线恢复。

下面按“组件 → 一次请求 → 关键机制 → 工作台 → 配置 → 与设计文档的对应”说明。

## 1. 组件

```
浏览器
├─ 工作台 web/（React + Ant Design X，构建后由 Agent 服务在 /workbench/ 提供）
│    对话列表 · 对话（执行过程 · 确认卡片 · 答复）· 工作区（系统页面 · 结果 · 工具 · 审计）
│    └─ iframe：车队地图页面（systems/fleet/static/map.html）+ agent-bridge.js
│                      │ WebSocket /ws/bridge?system&client（注册界面工具、执行调用、上报界面状态）
│ WebSocket /ws/workbench?client（用户消息、停止、重新生成、确认决定 ↔ 运行事件）
│ REST /api/*（配置、对话、结果、工具目录、审计、反馈、用户直接调用界面工具）
▼
Agent 服务（agent/，FastAPI，端口 8000）
├─ 运行时 runtime.py        编排循环、停止 / 重新生成、确认等待、事件推送
├─ 对话 conversations.py     每个对话一个 JSON：标题、模型历史、可重放事件、反馈
├─ 模型接入 llm.py           OpenAI 兼容接口（流式，含思考过程）；未配置时用 demo_planner.py
├─ 能力网关 gateway.py       工具目录 · 参数校验 · 风险策略 · confirm_token · 审计
│   ├─ API 适配   providers/openapi_provider.py   OpenAPI + 接入清单 → 工具 → HTTP 调用
│   ├─ 前端桥接   providers/bridge_provider.py    页面注册的界面工具 → WebSocket 调用
│   └─ 内置能力   providers/builtin_provider.py   分组 · 画布展示 · 结果查看与统计
├─ 结果仓库 results.py       result_id 句柄、类型识别、格式转换（data/results/）
├─ 审计 audit.py             data/audit.jsonl
└─ /mcp                      MCP 调试端点（tools/list、tools/call 子集）
▼ HTTP（X-Agent-User / X-Request-Id / Idempotency-Key）
业务系统（systems/，各自独立进程）
├─ 车队追踪地图台 :8101    REST API + 地图页面
├─ 排班系统 :8102          REST API
└─ 调度系统 :8103          REST API（司机组预览 / 提交）
```

| 组件 | 职责 | 不做什么 |
| --- | --- | --- |
| 工作台 | 管理对话；展示每一步工具调用；弹确认卡片；内嵌系统页面；展示结果 | 不直接调用业务系统 |
| 运行时 | 选工具、调模型、执行工具调用、把结果回填给模型、请求确认、推送事件 | 不处理具体系统的格式和鉴权 |
| 对话存储 | 保存模型历史（继续对话用）和工作台事件（重放用） | 不保存业务主数据 |
| 能力网关 | 统一的工具入口；校验参数；按风险等级放行或要求确认；记审计 | 不做规划，不保存对话 |
| 适配器 | 把一种能力来源变成工具：API、界面、内置 | 不决定何时调用 |
| 结果仓库 | 保存工具产出的列表数据，按需转换格式 | 不保存业务主数据 |
| 业务系统 | 按原样提供 API 和页面 | 不感知 Agent（只多了桥接 SDK 那几十行） |

两个标识要分清：

- **对话 ID**（`conversation_id`，`c_` + 12 位十六进制）：一段对话，跨刷新、跨重启存在；结果、确认令牌、审计都按它归属。
- **窗口 ID**（`client_id`，`wb_` 开头）：一个工作台浏览器标签页，保存在 sessionStorage，刷新不变。内嵌的系统页面用它和工作台配对：Agent 要操作“车队地图”时，找的是**这个窗口里**的那个地图页面。

## 2. 一次请求的链路（以“司机组重组并同步到调度系统”为例）

1. 用户在新对话里输入；工作台通过 `/ws/workbench` 发送 `user_message`（不带 `conversation_id`）。服务端创建对话，回 `conversation_created`，工作台把地址改成 `#/c/<对话 ID>`。
2. 运行时开始一次运行（`run_started`），由网关给出本次可用工具：API 工具 + 本窗口已接入页面注册的界面工具 + 内置工具。工具总数超过 30 个时按清单 `keywords` 只加载相关系统。
3. 运行时组装系统提示词（规则、已打开的界面、界面状态、本对话结果列表、相关系统的 SKILL.md），流式调用模型；文字和思考过程以 `text_delta` / `reasoning_delta` 推给工作台。
4. 模型返回工具调用 `tms__search_shift_drivers` → 网关校验参数 → API 适配器按 OpenAPI 拼出 HTTP 请求 → 排班系统返回 38 条 → 网关存为结果 `res_A`，只把摘要、总数、字段和 5 条预览交给模型。
5. `fleet__get_driver_positions(driver_ids="res_A")`：清单声明了 `accepts_result: {as: ids, field: driver_id}`，网关取出 `res_A` 的 `driver_id` 列，以逗号分隔传给车队系统 → `res_B`（带坐标，自动识别为 `feature_collection`）。
6. `analytics__run_grouping(points="res_B", groups=5, max_size=8)`：内置分组 → `res_C`（`group_plan`）。
7. 同一轮并行调用两个展示工具：`fleet__show_result_layer` 经桥接 WebSocket 发到本窗口的地图页面，页面调用自己的 `addTempLayer` 叠加分组；`canvas__show` 发出 `render` 事件，工作台在“结果”页签展示分组。工作台的规则：这一轮操作了系统页面时，停留在系统页签，“结果”页签只亮提示点。
8. `dispatch__preview_regroup(region, groups="res_C")`：网关把 `res_C` 转成分组列表，经 `adapter_hooks` 映射成调度系统的字段，调用预览接口；成功后网关签发 `confirm_token`，并保存这次预览的参数。
9. `dispatch__apply_regroup(confirm_token)`：网关核对令牌（存在、未过期、属于本对话、工具匹配），通过运行时向工作台发 `confirm_request`（含变更清单）并等待。用户点“确认提交”后，网关**回放预览时保存的参数**调用提交接口，`Idempotency-Key` 为该令牌。
10. 模型给出最终答复（`message`），运行结束（`run_finished`，演示模式附带追问建议）。每次工具调用都已写入审计日志；需要重放的事件已写入对话文件。

## 3. 关键机制

### 3.1 工具契约与返回信封（contracts.py）

工具描述字段与 MCP 对齐：`name`、`title`、`description`、`inputSchema`、风险等级、来源。同一份目录可转换为 OpenAI 兼容的 `tools`（给模型）或 MCP 的 `tools/list`（给 MCP 客户端，风险与来源放在 `_meta` 的 `io.baton/risk`、`io.baton/kind`）。

所有工具返回同一种信封，模型只看到精简版本（变更清单等只给界面的字段不进模型上下文）：

```json
{"ok": true, "summary": "已获取 38 名司机的最新位置", "result_id": "res_75b2b5cf",
 "result_type": "feature_collection", "total": 38, "fields": ["driver_id", "name", "lng", "lat"],
 "preview": [{"driver_id": "D0001", "lng": 121.23, "lat": 31.35}],
 "next_hints": ["可把 result_id 交给系统界面工具或 canvas__show 在地图上展示"]}

{"ok": false, "error_code": "HTTP_400", "message": "车队追踪地图台 返回 400：未知区域 huadong",
 "fix_hint": "检查参数取值是否符合工具描述", "retryable": false}
```

网关从不向上抛异常：参数不合法、系统报错、界面未打开、令牌无效等都变成带 `fix_hint` 的错误信封，模型据此自我修正。

### 3.2 结果仓库与引用（results.py）

- API 返回的列表自动识别：GeoJSON → `feature_collection`；含 `lng`/`lat` 的记录 → 转为点要素；其他列表 → `table`；小对象直接内联，大对象存为 `object`。
- 参数通过清单 `accepts_result` 接收 `result_id`，网关负责转换（`ids` / `rows` / `geojson` / `group_plan`）。
- 浏览器端（地图页面、工作台“结果”页签）通过 `GET /api/results/{id}?format=geojson|rows|full` 读取完整数据。
- 结果默认保存 24 小时（内存 + `data/results/*.json`）。

### 3.3 写操作：预览 → 确认 → 提交（gateway.py）

- 清单把写操作拆成两个工具：预览（`risk: read` + `pairs_with`）与提交（`risk: write|critical`）。
- 提交工具的参数只有 `confirm_token`；令牌由**网关**在预览成功后签发，绑定对话、10 分钟有效，并保存预览时的完整参数。模型无法伪造令牌，也无法在预览之后改参数。
- 确认由运行时强制：网关调用 `ctx.confirm(entry)`，运行时向工作台推送确认卡片并等待用户决定；无确认通道（如 `/mcp`）时直接拒绝写入。工作台的直接操作接口 `/api/ui-actions` 只接受界面工具（只改界面或只读）。
- 用户点确认之后的提交不会被“停止”打断（`asyncio.shield`）：停止只取消等待中的部分；已确认的提交完成后照常写审计，并注明“任务停止后完成”。
- 提交成功后令牌标记为已使用；重复调用返回第一次的结果，并带 `Idempotency-Key` 防止系统侧重复执行。

### 3.4 前端桥接协议（/ws/bridge）

```
页面 → 中心  {"type":"register","tools":[{"name","title","description","inputSchema"}]}
中心 → 页面  {"type":"registered","accepted":[...],"rejected":[{"tool","reason"}]}
中心 → 页面  {"type":"call","call_id":"…","tool":"show_result_layer","args":{…}}
页面 → 中心  {"type":"result","call_id":"…","result":{"ok":true,"summary":"…","data":…}}
页面 → 中心  {"type":"view_state","state":{…}}          // ≤ 2KB
```

- 连接参数：`system`、`client`（工作台窗口 ID，内嵌时由 URL 参数 `agent_client` 传入；旧参数名 `agent_session` 仍兼容）、`page`。
- 注册时按清单白名单过滤；风险等级以清单为准；工具显示名以清单 `title` 为准，其次是页面注册时的 `title`。
- 页面断开时工具自动下线，未完成的调用返回 `UI_DISCONNECTED`；连接变化以 `bridge_status` 推给所有工作台窗口。
- 调用超时 20 秒（`AGENT_UI_CALL_TIMEOUT_S`）。

### 3.5 工作台事件协议（/ws/workbench?client=<窗口 ID>）

客户端发送：

| 消息 | 字段 | 说明 |
| --- | --- | --- |
| `user_message` | `text`，`conversation_id`（可空） | 不带对话 ID 时新建对话 |
| `open` | `conversation_id` | 之后该对话的事件推给当前窗口（换窗口继续同一对话时用） |
| `stop` | `conversation_id` | 停止正在执行的任务 |
| `regenerate` | `conversation_id` | 撤掉最后一次运行，用同一句话重新运行 |
| `confirm_decision` | `conversation_id`、`confirm_id`、`approved` | 确认卡片的决定 |
| `ping` | — | 保活，回 `pong` |

服务端推送：

| 事件 | 含义 | 保存并重放 |
| --- | --- | --- |
| `hello` | 连接建立，附当前桥接状态 | 否 |
| `conversation_created` | 新对话的 ID、标题、时间 | 否 |
| `run_started` / `run_finished` / `run_error` | 一次运行的开始、结束（`completed` / `stopped` / `error` / `interrupted`，附追问建议）、失败 | 是 |
| `text_delta` / `reasoning_delta` | 逐字输出的答复 / 思考过程 | 否（由 `thought`、`message` 保存最终文本） |
| `thought` | 模型在调用工具前说的话及思考过程 | 是 |
| `tool_call_started` / `tool_call_finished` | 每次工具调用：标题、所属系统及其标识色、类型、风险、参数、结果摘要、错误、耗时 | 是 |
| `confirm_request` / `confirm_resolved` | 写操作确认卡片及其结果（确认、取消、超时、停止、中断） | 是 |
| `render` | 在“结果”页签展示某个 `result_id` | 是 |
| `message` | 最终答复（停止时为已输出的部分，标记 `partial`） | 是 |
| `run_removed` | 重新生成时撤掉的运行 | 否 |
| `bridge_status` | 各系统页面的桥接连接状态 | 否 |
| `error` | 操作被拒绝的原因（如对话不存在、正在执行） | 否 |

运行相关的事件都带 `conversation_id` 和 `run_id`；需要重放的事件另带递增的 `seq`。

### 3.6 对话保存、重放与恢复（conversations.py、web/src/store.ts）

- 每个对话一个文件 `data/conversations/<对话 ID>.json`：标题、时间、模型历史（继续对话时发给模型）、事件列表（重放用）、反馈。
- 工作台只有一套逻辑：把事件依次交给同一个 reducer 得到对话视图。实时事件是这样，打开历史对话时拉取的事件列表也是这样，所以刷新页面、切换对话后看到的执行过程、确认卡片、答复与实时看到的一致。
- 打开对话时，拉取快照期间到达的实时事件先缓存，快照应用后再补上；靠 `seq` 去重（`seq` 只增不减，重新生成撤掉的事件不回收序号）。
- 断线重连后，工作台重新拉取对话列表和当前对话，其他已打开过的对话标记为需刷新，下次打开时重新拉取。
- 停止：取消运行任务；待确认的写操作作废（`confirm_resolved`，原因 `stopped`）；已输出的文字作为不完整答复保存；模型历史补齐悬空的工具结果并以说明收尾，下一轮可以继续对话。
- 服务关闭：正在执行的任务记为 `interrupted`（不是“用户停止”）并保存；进程被强杀时，下次启动按文件补记中断。待确认的写操作不会在重启后被提交。

### 3.7 演示模式（demo_planner.py）

未配置模型时，用规则脚本代替模型“规划”，按步骤产出与模型相同格式的工具调用，覆盖几类演示指令，并把最终答复按字逐段推送、附带追问建议。它与真实模型走同一条链路（运行时、网关、适配器、桥接、确认、审计），用于在没有模型密钥时验证架构与接入规则。配置 `LLM_BASE_URL` 与 `LLM_MODEL` 后自动切换为真实模型。

### 3.8 MCP 调试端点（POST /mcp）

把网关工具目录以 MCP JSON-RPC 暴露，实现 `initialize`、`tools/list`、`tools/call`，便于用 MCP 客户端检查工具定义和调用结果。该端点没有确认通道，写操作一律拒绝。

## 4. 工作台（web/）

技术栈：React 19、Ant Design 6、Ant Design X 2（对话类组件）、Vite。构建产物 `web/dist` 由 Agent 服务在 `/workbench/` 提供；未构建时该地址显示构建提示。

| 区域 | 用到的组件 | 说明 |
| --- | --- | --- |
| 对话列表 | `Conversations` | 按时间分组；执行中、待确认状态；重命名、删除；搜索 |
| 新对话页 | `Welcome`、`Prompts` | 已接入系统、常用任务（下方色条表示会经过哪些系统） |
| 消息 | `Bubble.List` | 用户消息靠右；助手回复占满整列 |
| 思考过程 | `Think` | 模型返回 `reasoning_content` 时显示，默认折叠 |
| 执行过程 | `ThoughtChain`（自定义节点） | 每次工具调用一个站点，颜色是系统标识色；运行中、出错、待确认时展开，完成后折叠为线路缩略 |
| 答复 | `XMarkdown` | 流式渲染；答复里的结果编号可点击 |
| 输入框 | `Sender`、`Suggestion` | 执行中变为停止按钮；输入 `/` 选择常用任务 |
| 工作区 | `Tabs` + iframe | 系统页面始终挂载（收起工作区时只是隐藏），保证界面工具随时可用 |

视觉约定（`web/src/theme.ts`）：主色为信号青；琥珀色只用于写操作和确认，凡是会改业务数据的东西都带它；每个接入系统有自己的标识色（清单 `color`，未声明时按顺序分配，避开琥珀、绿、红），像地铁线路色一样贯穿执行过程、页签和工具目录；平台内置能力用灰色。

页签切换规则：界面工具执行成功后切到该系统页签；`render` 事件切到“结果”页签，但同一轮已经操作了系统页面时只亮提示点；窄屏下不自动弹出工作区，只在工作区按钮上提示。

## 5. 配置（环境变量，见 .env.example）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_NAME` / `APP_TAGLINE` | Baton / 多系统编排台 | 产品名与副标题（工作台标题、侧栏、服务名） |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | 空 | OpenAI 兼容接口；不填为演示模式 |
| `LLM_TIMEOUT_S` / `LLM_TEMPERATURE` | 90 / 0.2 | 模型调用超时与温度 |
| `LLM_STREAM` | true | 流式输出；接口不支持时自动退回一次性返回 |
| `DEMO_STREAM_DELAY_S` | 0.012 | 演示模式模拟逐字输出的间隔，0 为不模拟 |
| `AGENT_HOST` / `AGENT_PORT` | 127.0.0.1 / 8000 | Agent 服务监听地址 |
| `AGENT_PUBLIC_URL` | http://localhost:8000 | 浏览器访问 Agent 的地址（用于 CORS） |
| `AGENT_DATA_DIR` | ./data | 对话、结果、审计、反馈的存放目录 |
| `AGENT_WEB_DIR` | ./web/dist | 工作台构建产物目录 |
| `AGENT_MANIFESTS` | systems/*/manifest.yaml | 接入清单位置 |
| `AGENT_MAX_STEPS` | 12 | 单次运行最多调用模型的轮数 |
| `AGENT_CONFIRM_TIMEOUT_S` | 600 | 等待用户确认的时长 |
| `AGENT_UI_CALL_TIMEOUT_S` | 20 | 界面工具调用超时 |
| `AGENT_FULL_CATALOG_LIMIT` | 30 | 工具数超过该值时按关键词路由 |
| `AGENT_RESULT_TTL_S` | 86400 | 结果保存时长 |
| `FLEET_URL` / `TMS_URL` / `DISPATCH_URL` | localhost:8101/8102/8103 | 模拟系统地址（清单中引用） |

## 6. 与设计文档 v0.1 的对应

| 设计文档章节 | v0.2 实现 | 说明 |
| --- | --- | --- |
| 2 总体架构 | 已实现 | 工作台 / 运行时 / 能力网关 / 三类能力来源 / 业务系统 |
| 3.1 API 适配 | 已实现 | OpenAPI 白名单 + 描述重写 + 结果引用 + 钩子 |
| 3.2 前端桥接 | 已实现（内嵌模式） | 按工作台窗口配对；独立模式（系统自己的标签页）未做 |
| 3.3 自动化兜底 | 未实现 | 后续版本 |
| 3.4 接入清单 | 已实现 | 字段见接入规则第 10 节；`python -m agent.check` 校验 |
| 4 Function Calling 规范 | 已实现 | 命名、信封、错误、两段式写入、关键词路由；向量检索与程序化调用未做 |
| 5 结果与可视化 | 部分实现 | 结果仓库、回填原系统、工作台“结果”页签；MCP Apps 未做 |
| 6 运行时 | 部分实现 | 单编排 Agent、知识包、界面状态、模型网关、对话保存、停止与重新生成；长任务、子 Agent 未做 |
| 7 安全与治理 | 部分实现 | 风险分级、确认、审计（区分 Agent 与用户直接操作）、令牌绑定对话；SSO、Token Exchange、角色过滤、OpenTelemetry 未做 |

## 7. 扩展点

- 新增能力来源：在 `agent/providers/` 下实现 `specs()` 与 `call()`，在网关 `_dispatch` 中按 `kind` 分发。
- 身份：在 `server.py` 中从 SSO 令牌解析用户替换 `demo_user`；在 `openapi_provider.py` 的请求头处加入令牌交换。
- 持久化：对话目前是 JSON 文件，确认令牌在进程内存，结果仓库是内存 + 文件；替换为 PostgreSQL / Redis 时接口不变。
- 模型：`llm.py` 的 `LLM` 协议只要求实现 `complete(messages, tools, on_delta=None)`。
- 事件协议：目前为自定义事件，字段已按“运行 → 步骤 → 确认”组织；后续可映射到 AG-UI 事件模型，工作台与运行时之间的边界不变。
