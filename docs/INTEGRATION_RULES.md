# 系统接入规则 v1

适用版本：Baton v0.2（v0.1 时名为 Agent Hub，规则本身未变，v0.2 只补充了 R5.5–R5.7 和清单的 `color`、界面工具 `title` 两个可选字段；未发布版本新增第 11 节场景剧本与清单的 `playbooks` 字段）。规则编号（R1.1 等）与代码中的校验提示一一对应；标注 **[校验]** 的规则由 `agent/manifest.py`、网关或 `python -m agent.check` 自动检查，违反时拒绝加载或拒绝注册。

一句话：**一个系统 = 一份接入清单（manifest.yaml）+ 可选的知识包（SKILL.md）+ 可选的格式钩子（adapter_hooks.py）+ 可选的场景剧本（playbooks.yaml）**。系统后端不改代码；需要界面能力时，前端加一个 SDK 和几个处理函数。

---

## 0. 接入方式与选择顺序

| 能力来源 | 什么时候用 | 系统侧要做的事 | v1 状态 |
| --- | --- | --- | --- |
| API（OpenAPI → 工具） | 能力在后端：查询、统计、计算、提交 | 提供 OpenAPI 文档；清单里列白名单 | 已实现 |
| 界面（前端桥接 SDK） | 能力只在前端：定位、叠加图层、播放、截图 | 页面引入 `agent-bridge.js`，每个操作写一个处理函数 | 已实现 |
| 内置（平台提供） | 与具体系统无关：分组、画布展示、结果统计 | 无 | 已实现 |
| 自动化兜底（浏览器脚本） | 老系统：无接口、前端不能改 | 无 | 未实现，列入后续版本 |

选择顺序：能走 API 就不走界面；能用界面 SDK 就不用自动化。

## 1. 命名

- **R1.1 [校验]** `system` 为小写字母开头的 2–20 位小写字母或数字，如 `fleet`、`tms`。全平台唯一。
- **R1.2 [校验]** 工具全名为 `<system>__<tool>`；`tool` 为小写 snake_case（2–41 位）；全名只含小写字母、数字、下划线，不超过 64 个字符。原因：部分模型接口限制函数名字符集和长度，双下划线做命名空间最稳妥。
- **R1.3** 读写语义统一用以下动词：`search`（按条件查列表）、`get`（按 ID 取对象）、`run`（执行计算并产出新结果）、`show`（只改界面的展示）、`preview`（写操作预览）、`apply`（确认后执行写操作）。界面操作可用领域动词（如 `play_track`、`focus_driver`），但不得冒用 `preview` / `apply`。
- **R1.4 [校验]** 同一系统内工具名唯一（API 工具与界面工具合并计算）。

## 2. 后端 API（API 适配）

- **R2.1 [校验]** 提供 OpenAPI 3.x JSON 文档（URL 或文件）；要暴露的每个接口必须有唯一 `operationId`。清单里引用不存在的 operationId 会报错。
- **R2.2** 请求、响应均为 JSON。出错时返回 4xx/5xx，响应体带 `detail` 或 `message` 字段说明原因（网关会原样转成给模型的错误信息，并按状态码附加修正提示）。
- **R2.3** 列表接口返回数组，或 `{ "items": [...] }`（也识别 `data`、`results`、`rows`、`records`、`list`；其他位置用清单 `result.list_path` 指定）。含坐标的记录用 `lng` / `lat` 字段（也识别 `lon`、`longitude`、`latitude`），或直接返回 GeoJSON FeatureCollection。
- **R2.4** 单次返回不超过 5000 条（结果仓库上限），超过的部分会被截断；数据量大时提供筛选参数或分页。
- **R2.5 [校验]** 只有清单 `api.expose` 白名单中的接口会成为工具；每个工具的 `description` 在清单里重写（见 R6.1），不直接使用 OpenAPI 的 summary。
- **R2.6** 数组参数放在 query 中时，平台传逗号分隔字符串（如 `driver_ids=D0001,D0002`）；接口参数类型声明为 array 时则按重复参数传。
- **R2.7** 时间用带时区的 ISO 8601；单位写进字段名（`radius_m`、`duration_min`、`lookback_h`）。
- **R2.8** 系统格式与平台约定不一致时，用清单 `hooks.request` / `hooks.response` 指定一个 Python 函数做格式映射，签名为 `hook(args: dict, ctx: dict) -> dict`。钩子只做字段映射，不写业务逻辑。
- **R2.9** 平台调用系统时附带请求头 `X-Agent-User`、`X-Agent-Session`（对话 ID）、`X-Request-Id`，系统可写入自己的日志，便于追踪。

## 3. 写操作

- **R3.1 [校验]** 修改业务数据的操作必须提供**成对接口**：预览（不落库，`risk: read`）和提交（落库，`risk: write` 或 `critical`）。在预览工具上声明 `pairs_with: <提交工具名>`。没有预览的写操作不允许接入。
- **R3.2 [校验]** 预览与提交的参数集合必须一致（网关加载时比对）。提交工具不在清单中声明 `params`：模型只能传入 `confirm_token`，网关回放预览时的参数，保证“用户看到的就是提交的”。
- **R3.3** 预览接口返回 `{ "summary": "一句话", "changes": [ {...}, ... ] }`：`summary` 写给人和模型看，`changes` 每条一个变更（如 `driver_id`、`from_group`、`to_group`），用于确认卡片。
- **R3.4** 提交接口必须支持 `Idempotency-Key` 请求头：同一个键重复提交时返回第一次的结果，不重复执行。平台用 `confirm_token` 作为该键。
- **R3.5 [校验]** 风险等级：`write` = 可回滚的业务修改；`critical` = 不可逆或影响面大（v1 与 write 同样需要确认，后续版本加二次确认和角色限制）。
- **R3.6** 确认由运行时强制：网关签发的 `confirm_token` 10 分钟有效、只属于发起它的会话；用户在工作台点“确认”后才调用提交接口。没有工作台确认通道的调用（如 MCP 调试端点）一律拒绝写入。

## 4. 结果引用

- **R4.1** 列表类结果由网关存入结果仓库，模型只拿到 `result_id`、摘要、总数、字段名和最多 5 条预览。结果默认保存 24 小时，只对产生它的用户可见。
- **R4.2** 参数需要接收上一步的结果时，在清单 `params.<参数名>.accepts_result` 中声明转换方式，模型直接传 `result_id`：

  | `as` | 转换结果 | 典型用途 |
  | --- | --- | --- |
  | `ids` | 取某个字段的值列表（`field` 指定字段，如 `driver_id`）；也接受逗号分隔的字面值 | 名单 → 批量查询 |
  | `rows` | 行数据列表 | 批量导入 |
  | `geojson` | FeatureCollection | 空间计算 |
  | `group_plan` | `[{"group_no": 1, "member_ids": [...]}]` | 分组方案写回 |

- **R4.3** 结果类型：`table`、`feature_collection`、`group_plan`、`object`。系统不需要了解结果仓库；转换在网关完成，格式差异用 R2.8 钩子处理。

## 5. 前端桥接（界面工具）

- **R5.1** 页面引入 `<Agent 地址>/sdk/agent-bridge.js`，调用 `AgentBridge.createAgentBridge({ system })` 后用 `registerTool({ name, title, description, inputSchema, execute })` 注册工具（`title` 是给人看的名称，显示在工作台的执行过程里；清单里写了 `title` 时以清单为准）。接口形态对齐 WebMCP；浏览器提供原生 `document.modelContext` 时 SDK 会同时注册原生工具。
- **R5.2 [校验]** 能注册的工具必须在清单 `ui_bridge.tools` 白名单中声明，风险等级以清单为准，且只能是 `view`（只改界面）或 `read`（只读界面状态）。界面工具不得修改业务数据，业务写入一律走 API 的预览 / 提交（第 3 节）。未声明的工具注册时被拒绝。
- **R5.3** `execute` 只调用页面已有方法，返回 `{ ok, summary, data? }` 或一个字符串；失败时返回 `{ ok: false, error: { code, message, fix_hint } }`。单次执行须在 20 秒内完成（`AGENT_UI_CALL_TIMEOUT_S`），耗时操作立即返回“已开始”，不要等待完成。
- **R5.4** 可选 `provideViewState(() => state)` 提供界面状态（视图范围、选中对象 ID、已开图层），不超过 2KB；视图变化时调用 `notifyViewChanged()`。状态会随每轮对话进入模型上下文，用来理解“当前视图里”“我选中的这几个”。
- **R5.5** 页面需允许被工作台以 iframe 嵌入（CSP `frame-ancestors` 包含工作台地址），并在清单 `ui_bridge.embed_url` 声明内嵌地址；工作台会追加 `?agent=<Agent 地址>&agent_client=<工作台窗口 ID>` 参数，SDK 默认从这两个参数读取连接信息（旧参数名 `agent_session` 仍兼容）。不带参数打开时页面照常独立运行。
- **R5.6** Agent 优先操作发起对话的那个工作台窗口里内嵌的页面；没有时使用同一用户最近打开的页面。页面关闭或断开时，对应界面工具从可用列表中消失。
- **R5.7** 界面工具可能在页面不可见时被调用：工作台收起工作区、或停留在别的页签时，内嵌页面仍在运行但尺寸为 0。依赖尺寸的操作（缩放到范围、测量、截图）要等页面可见后再执行，例如记下待执行的缩放，在 `resize` 时补做（参考 `map.html` 中的 `fitBounds`）。工具本身照常返回成功。

## 6. 描述与知识包

- **R6.1 [校验]** 每个工具的 `description` 必填，8–400 字，写清四件事：做什么、什么时候用、什么时候不用（改用哪个工具）、返回什么。参数复杂的工具在清单里加 1–3 个 `examples`。
- **R6.2** 每个系统建议提供 `SKILL.md`（对齐 Agent Skills 格式：YAML frontmatter 含 `name`、`description`，正文写术语、典型操作套路、坑点），不超过 2000 字。被选中的系统，其知识包会进入系统提示词。

## 7. 坐标与单位

- **R7.1 [校验]** 工具输入输出统一 WGS84（`crs: EPSG:4326`），几何用 GeoJSON，坐标顺序 [经度, 纬度]。系统内部使用 GCJ-02、BD-09 或 CGCS2000 时，在系统接口或 R2.8 钩子中转换，不要把非 WGS84 坐标交给平台。
- **R7.2** 距离用米或公里并写进字段名，时长用分钟，时间带时区。

## 8. 身份与安全

- **R8.1 [校验]** `auth.mode` 取值：`none`（仅限本地开发与测试）、`static_bearer`（从 `token_env` 指定的环境变量读取令牌，加到 `Authorization: Bearer`）。以用户身份换取令牌的 `token_exchange` 在 v1 中未实现，配置会被拒绝。
- **R8.2** 系统必须自行校验权限，不能因为请求来自 Agent 就放行；界面工具运行在登录用户的页面里，后端接口同样要鉴权。
- **R8.3** 所有工具调用由网关写入审计日志 `data/audit.jsonl`（用户、会话、工具、风险、参数摘要、结果、耗时、是否经确认）。
- **R8.4** 系统返回的文本（备注、地址等）只作为数据使用；不要在返回值里放给模型的指令。

---

## 9. 接入步骤（检查清单）

1. 确认系统的 OpenAPI 文档可访问，要暴露的接口都有 `operationId`。
2. 新建 `systems/<system>/manifest.yaml`：填 `system`、`title`、`base_url`、`keywords`，在 `api.expose` 里逐个列出接口，重写 `description`，标风险等级。
3. 需要接收上一步结果的参数，加 `params.<名>.accepts_result`；固定值参数用 `fixed` 隐藏。
4. 有写操作：补齐预览 / 提交成对接口，预览工具加 `pairs_with`，提交接口支持 `Idempotency-Key`。
5. 格式对不上：写 `adapter_hooks.py` 并在清单 `hooks` 中引用。
6. 需要界面能力：页面引入 SDK，注册工具（参考 `systems/fleet/static/map.html` 末尾），清单 `ui_bridge` 中声明白名单和 `embed_url`，并放开 iframe 嵌入。
7. 写 `SKILL.md`。
8. 可选：为常见任务写场景剧本 `playbooks.yaml`（第 11 节），清单里加 `playbooks: playbooks.yaml`。用 `GET /api/intents?text=<一句提问>` 检查能否识别、参数取得对不对。
9. 启动系统后运行 `python -m agent.check systems/<system>/manifest.yaml -v`，应为 0 个错误；再在工作区“工具”页签确认工具和描述。
10. 在工作台用 3–5 句真实提问试跑（有剧本的场景在规则模式和模型模式各跑一遍），在工作区“审计”页签核对每次调用。

## 10. 清单字段参考

```yaml
manifest_version: 1                 # 固定为 1
system: fleet                       # R1.1 命名空间
title: 车队追踪地图台                # 显示名称
description: 一句话说明系统是做什么的
color: "#2563EB"                    # 可选：系统标识色 #RRGGBB，工作台的执行过程、页签、工具目录用它区分系统；
                                    # 不填时按顺序分配。不要用琥珀色（工作台用它表示写操作）
owner: gis-team                     # 可选：负责团队
base_url: ${FLEET_URL:-http://localhost:8101}   # 支持 ${环境变量:-默认值}
crs: EPSG:4326                      # R7.1 只能是 EPSG:4326
keywords: [车队, 司机, 地图]         # 工具超过 30 个时按关键词路由，只加载相关系统
auth:
  mode: none                        # none | static_bearer（R8.1）
  token_env: FLEET_TOKEN            # static_bearer 时必填

api:
  openapi: ${FLEET_URL:-http://localhost:8101}/openapi.json   # URL 或相对清单的文件路径
  base_url: http://...              # 可选：接口根地址，默认同上面的 base_url
  expose:                           # R2.5 白名单
    - operation: getPositions       # OpenAPI operationId（R2.1）
      tool: get_driver_positions    # 工具名，全名 fleet__get_driver_positions（R1.2）
      title: 司机位置                # 可选
      description: 查询一批司机的最新位置……（R6.1）
      risk: read                    # read | write | critical（R3.5）
      pairs_with: apply_x           # 仅预览工具：成对的提交工具（R3.1）
      params:
        driver_ids:
          accepts_result: {as: ids, field: driver_id}   # R4.2
          description: 覆盖参数说明
          default: ...              # 可选：默认值
        asset_type: {fixed: trailer}                    # 固定值，不暴露给模型
      result:
        type: auto                  # auto | table | feature_collection | object
        list_path: items            # 列表在返回体中的位置（R2.3）
        summary: "已获取 {total} 名司机的位置"           # 摘要模板，可用 {total}
      hooks:
        request: systems.dispatch.adapter_hooks:group_plan_to_body   # R2.8
        response: ...
      examples:                     # 可选：调用示例（R6.1）
        - {driver_ids: res_1a2b3c4d}

ui_bridge:
  enabled: true
  title: 车队地图                    # 工作台页签名
  embed_url: ${FLEET_URL:-http://localhost:8101}/map   # R5.5
  tools:                            # R5.2 白名单 + 风险等级（view | read），title 为显示名（R5.1）
    - {tool: show_result_layer, risk: view, title: 叠加结果图层}

skill: SKILL.md                     # R6.2 知识包，相对清单路径
playbooks: playbooks.yaml           # 可选：场景剧本，相对清单路径（第 11 节）
```

## 11. 场景剧本（可选）

场景剧本把“用户常说的一类任务”写成声明式的步骤，同一份剧本有三种用途（ADR-0011）：

| 用途 | 效果 |
| --- | --- |
| 规则模式（未配置模型） | 按剧本识别并执行，不需要为新场景写 Python |
| 模型模式 | 识别结果、已取出的参数和建议步骤写进系统提示词（标明“仅供参考”），模型仍可自行决定 |
| 工具路由 | 工具多时，识别出的意图引用的系统一定会被加载；追问时上一轮用过的系统也会保留 |

另外，可单独发起的意图（没有 `requires.result`）会作为工作台“常用任务”，规则模式的帮助文本也由剧本自动生成。

- **R11.1 [校验]** 剧本文件由清单 `playbooks` 指向（相对清单路径），文件必须存在。意图 `id` 为小写字母开头的 snake_case，全平台唯一。跨系统的场景放在“产出最终结果 / 被写入”的那个系统目录下；引用的系统未接入时，该意图只是不可用（不参与识别），`agent.check` 会提醒。
- **R11.2 [校验]** `match` 至少声明 `any`、`all`、`slots` 之一：`none` 命中任何一个即排除，`all` 须全部命中，`any` 至少命中一个，`slots` 中的槽位须从原话里取到值（这些槽位不能有 `default`）。以 `re:` 开头的词按正则匹配，须能编译。至少一个 `examples`，第一个示例用于帮助文本和常用任务。
- **R11.3 [校验]** 步骤须且只能是 `call`（调用工具）、`show`（展示结果）、`write`（写操作）之一；步骤 id、槽位名、`requires.result` 名不能重名。引用的工具名须符合 R1.2，且（系统已接入时）在对应清单的 `api.expose` 或 `ui_bridge.tools` 中声明、或是平台内置工具。`requires` 每项须且只能是 `tool`、`result`（须带 `type`）、`slot` 之一。
- **R11.4 [校验]** 模板里引用的名字必须已定义：槽位、步骤 id、`requires.result` 的名字，以及全局可用的 `examples`、`help`、`today`、`intent`；`on_error` 里还可用 `{error}`，`write.lines` 里还可用 `{target}`、`{summary}`、`{error}`、`{state}`。`when` 引用槽位或步骤 id；`suggestions.by` 须是 `write` 步骤。引用其他意图的示例 `{examples.<id>}` 时，该意图不存在或不可用则显示为空（`agent.check` 提醒）。
- **R11.5 [校验]** 槽位类型须是已注册的类型，参数须是该类型认识的参数。内置类型：

  | 类型 | 参数 | 取值 |
  | --- | --- | --- |
  | `choice` | `options: {取值: [触发词…]}` | 按声明顺序第一个命中的取值 |
  | `flag` | `any: [触发词…]` | 命中为 true，否则 false |
  | `date` | — | ISO 日期，或“今天 / 明天 / 后天 / 昨天”（按 `AGENT_TZ`），都没有时为今天 |
  | `number` | `before`、`unit`、`loose`、`decimal` | “不超过 8 人”这类数字，支持一到十的汉字 |
  | `duration` | `unit`（`min` / `h`）、`before`、`after` | “过去 24 小时”“2 小时以上”换算成指定单位 |
  | `pattern` | `pattern`、`group`、`all` | 正则提取，如司机 ID `D\d{4}` |
  | `assignments` | `id`、`verbs`、`unit`、`key` | “把 D0012 换到第 3 组” → `[{"id": "D0012", "group": 3}]` |

  所有类型都可用 `default`（取不到时的值）、`description`（写给模型的说明）、`from_call: <工具全名>.<参数名>`（先沿用本对话里该工具最近一次调用的参数，适合追问）。新的参数形态在 `agent/slots.py` 用 `@extractor("类型名", {参数…})` 注册。
- **R11.6 [校验]** 剧本不能绕过两段式写入（第 3 节）：`call` 步骤不得调用 `risk: write|critical` 的工具，也不能传 `confirm_token`；写操作只能用 `write` 步骤，`preview` 与 `apply` 属于同一系统，且清单里 `preview` 工具声明了 `pairs_with` 指向 `apply`；`write.args` 只传给 `preview`，不能包含 `confirm_token`。用户确认仍由运行时强制，规则模式里同样会弹出确认卡片。
- **R11.7** 剧本只描述“常见说法 → 常用步骤”，不要试图覆盖所有问法：模型模式下它只是提示，识别不到时模型照常自由规划。触发词宁少勿滥，相互重叠的意图用 `priority` 明确先后（同优先级按命中项数，再按声明顺序）。

模板语法：`{名字.路径}` 取值（如 `{plan.result_id}`、`{base.data.params.groups}`）；`{a|b|5}` 依次取第一个有值的备选，不是名字的备选按字面量（数字转为数字）。工具参数整段就是一个占位符时保留原类型（数字、列表），值为空的参数不传。步骤结果可引用的字段：`call` 步骤为工具返回的信封字段（`result_id`、`summary`、`total`、`data`…）加 `error`；`show` 步骤为 `where`（展示到了哪里）；`write` 步骤为 `state`（`skipped` / `preview_failed` / `not_applied` / `applied` / `rejected` / `failed`）、`line`（该状态的一句说明，可用 `lines` 覆盖默认文案）、`summary`、`error`。

```yaml
# systems/<system>/playbooks.yaml
version: 1
intents:
  - id: regroup                       # R11.1
    title: 重组司机组
    icon: team                        # 可选：常用任务图标（team / environment / node-index / car / swap / sync）
    description: 按当前位置把排班司机分组，确认后写回调度系统
    examples: [把华东区明天早班的司机分成 5 组，每组不超过 8 人，确认后同步到调度系统]
    priority: 90                      # 越大越优先
    match: {any: [分组, 重组, 分成]}   # R11.2
    slots:                            # R11.5
      region: {type: choice, options: {north-china: [华北, 北京]}, default: east-china}
      date: {type: date}
      groups: {type: number, before: [分成], unit: [组], loose: true, default: 5}
      sync: {type: flag, any: [同步, 写回]}
    requires:                         # 可选：前置条件，不满足时答复 message
      - tool: fleet__show_result_layer
        message: 请先打开车队地图
    steps:                            # R11.3
      - id: roster
        call: tms__search_shift_drivers
        args: {region: "{region}", date: "{date}", shift: early}
        on_error: 查询排班名单失败：{error}
      - id: plan
        call: analytics__run_grouping
        args: {points: "{roster.result_id}", groups: "{groups}"}
      - id: show
        show: {result: plan, color_by: group, title: 分组方案, ui: [fleet__show_result_layer], canvas: fallback}
      - id: writeback                 # R11.6
        when: sync
        write: {preview: dispatch__preview_regroup, apply: dispatch__apply_regroup, args: {groups: "{plan.result_id}"}}
    answer: |-                        # R11.4
      {plan.summary}，已在{show.where}展示。
      - {writeback.line}
    suggestions:
      by: writeback
      cases: {applied: ["{examples.stops}"]}
      default: [同步到调度系统]
```
