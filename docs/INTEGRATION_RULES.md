# 系统接入规则 v1

适用版本：Baton v0.2（v0.1 时名为 Agent Hub，规则本身未变，v0.2 只补充了 R5.5–R5.7 和清单的 `color`、界面工具 `title` 两个可选字段）。规则编号（R1.1 等）与代码中的校验提示一一对应；标注 **[校验]** 的规则由 `agent/manifest.py`、网关或 `python -m agent.check` 自动检查，违反时拒绝加载或拒绝注册。

一句话：**一个系统 = 一份接入清单（manifest.yaml）+ 可选的知识包（SKILL.md）+ 可选的格式钩子（adapter_hooks.py）**。系统后端不改代码；需要界面能力时，前端加一个 SDK 和几个处理函数。

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
8. 启动系统后运行 `python -m agent.check systems/<system>/manifest.yaml -v`，应为 0 个错误；再在工作区“工具”页签确认工具和描述。
9. 在工作台用 3–5 句真实提问试跑，在工作区“审计”页签核对每次调用。

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
```
