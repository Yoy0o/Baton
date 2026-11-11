# 更新日志

记录每个版本对用户、接入方和协议的变化；内部重构不记。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。每个版本下按“新增 / 变更 / 修复 / 移除”分组，没有的分组省略。

开发过程的细节见 [docs/devlog/](docs/devlog/README.md)，设计理由见 [docs/adr/](docs/adr/README.md)。

## [未发布]

### 新增

- `AGENTS.md`：给编码 Agent 的仓库速查（架构、命令、不能破坏的约束、常见改动位置、完成前检查、文档与留痕规则）。
- 开发留痕：本文件、`docs/adr/`（架构决策记录，补记 v0.2 基线决策 ADR-0001 至 0009）、`docs/devlog/`（开发日志）。
- 代码检查与 CI：`ruff check .`（配置在 `pyproject.toml`）；`.github/workflows/ci.yml` 跑 ruff、pytest、前端构建和 `web/dist` 一致性检查；`.gitattributes` 统一 LF 换行（ADR-0010）。
- Windows 上自动安装 `tzdata`，保证 `AGENT_TZ` 可识别。
- 场景剧本（ADR-0011，接入规则第 11 节，R11.1–R11.7）：清单新增可选字段 `playbooks`，指向同目录下的 `playbooks.yaml`，声明意图的触发词、优先级、槽位、前置条件、步骤（`call` / `show` / `write`）、答复模板与追问建议；加载时校验工具名、模板引用，并保证剧本不能绕过两段式写入。`fleet`、`dispatch` 附带剧本，覆盖原规则模式的 6 个场景。新的槽位类型在 `agent/slots.py` 用 `@extractor` 注册。
- 模型模式下，系统提示词新增“本轮任务识别”一节：识别出的意图、已取出的参数、建议步骤、未满足的前置条件（仅供参考，识别不到时不出现）。
- `GET /api/intents`：列出场景剧本；带 `text` 参数时只识别不执行，返回命中项、槽位和给模型的提示，供接入方调试。`GET /api/config` 新增 `presets`（可单独发起的意图），工作台的常用任务改由它生成，接入新剧本后自动出现。
- 工作台事件 `run_started` 新增可选字段 `intent`（`{id, title, system, score}` 或 `null`）。
- 网关新增错误码 `REPEATED_CALL`：同一次运行里，工具名和参数都相同、且上次以不可重试的错误失败的调用，直接拒绝并记审计，不再调用系统（界面未打开 `UI_NOT_CONNECTED` 除外）。
- `python -m agent.check` 列出各系统的场景，并提醒引用了未接入系统而不可用的场景。
- 预览版本校验（ADR-0013，接入规则 R3.7）：预览接口返回 `etag` 时，网关在提交时以 `If-Match` 请求头带回；系统答复 409 / 412（预览之后数据已变化）时，网关返回新错误码 `PREVIEW_STALE`、作废该确认令牌，不落库，需要重新预览并重新确认。调度系统样例已实现。预览不返回 `etag` 的系统行为不变。

### 变更

去掉 demo / 演示 命名。**升级时需要改 `.env` 和启动命令**：

| 原名 | 新名 |
| --- | --- |
| `python run_demo.py` | `python run.py` |
| 环境变量 `DEMO_STREAM_DELAY_S` | `RULE_STREAM_DELAY_S` |
| 环境变量 `AGENT_DEMO_USER`（默认 `demo`） | `AGENT_DEFAULT_USER`（默认 `local`） |
| `GET /api/config` 的 `mode: "demo"` | `mode: "rule"` |
| `agent/demo_planner.py`（`DemoPlanner`） | `agent/rule_planner.py`（`RulePlanner`） |
| `systems/_demo_data.py` | `systems/_sample_data.py` |
| 界面与文档中的“演示模式”“演示数据” | “规则模式”“样例数据” |

- 默认用户从 `demo` 改为 `local`：已有的本地对话按用户归属，升级后看不到旧对话时，把 `data/conversations/*.json` 里的 `"user": "demo"` 改为 `"local"`，或在 `.env` 中设置 `AGENT_DEFAULT_USER` 为旧值。
- `run.py` 的端口、监听地址、产品名、模型配置改从 `Settings` 读取，与 Agent 服务一致；前端构建产物目录跟随 `AGENT_WEB_DIR`。
- 规则模式改为“意图识别 + 场景剧本”执行，`rule_planner.py` 不再包含具体场景；原有 6 个场景的工具调用顺序、参数和答复不变。帮助文本按剧本自动生成，列出全部场景。
- 轨迹回放没有给出司机 ID 时，规则模式先询问是哪位司机（原来默认回放 D0012）。
- 工具路由：工具总数超过 `AGENT_FULL_CATALOG_LIMIT` 时，除了清单 `keywords` 命中的系统，也保留识别出的意图引用的系统和上一轮用过的系统，追问不再因为没提到关键词而丢工具。
- 工具调度：模型一次给出多个调用时，相邻的只读 / 只改界面调用并行执行，写操作单独执行（原来只要含一个写操作，整轮全部串行）；无参工具的参数为 `null` 时按空对象处理，不再报参数错误。

### 修复

- **写操作确认可以被别的网页绕过**（ADR-0012）：`/ws/workbench`、`/ws/bridge` 原先不校验 `Origin`，用户浏览器里的任意网页都能连上工作台通道、发起任务并自己回“确认”。现在 WebSocket 握手和会改状态的 HTTP 请求带了 `Origin` 且不在允许列表时被拒绝（403）；允许列表为 `AGENT_PUBLIC_URL`、各系统清单里的地址和新配置项 `AGENT_ALLOWED_ORIGINS`。**升级时注意**：用 `http://localhost:8000` 以外的地址访问工作台需要设置 `AGENT_PUBLIC_URL`；前端开发需要 `AGENT_ALLOWED_ORIGINS=http://localhost:5173`。接入规则新增 R5.8。
- 配置了模型时，系统提示词里的当前时间固定按东八区计算，不受 `AGENT_TZ` 影响；现在与规则模式一样按 `AGENT_TZ` 计算，并在提示词里写明时区和 UTC 偏移。
- 测试夹具清掉所有 `APP_` / `LLM_` / `AGENT_` / `RULE_` 前缀的环境变量，开发者终端里导出的配置不再影响测试结果。
- 文档与代码对齐：ARCHITECTURE 第 5 节补上已有但未记录的配置 `AGENT_HTTP_TIMEOUT_S`、`AGENT_DEFAULT_USER`、`AGENT_TZ`，并更正 `AGENT_PUBLIC_URL` 的用途说明；第 1 节补上请求头 `X-Agent-Session`；`.env.example` 补齐全部可选配置。

## [0.2.0] - 2026-10-28

本仓库的首次提交。v0.1 名为 Agent Hub，不在本仓库中。

### 新增

- 工作台重做为常规 Agent 工作台（React 19 + Ant Design 6 + Ant Design X 2）：左侧对话列表、中间对话、右侧工作区（业务系统页面、结果、工具、审计）。
- 对话保存与事件重放：刷新、断线重连、服务重启后如实还原；服务重启时进行中的任务和待确认写操作记为中断，不会误提交。
- 流式输出，展示模型返回的思考过程（`reasoning_content`）；执行中可停止，最后一条回复可重新生成。
- 产品名可用 `APP_NAME` / `APP_TAGLINE` 配置。
- 接入规则补充 R5.5–R5.7（iframe 内嵌、按工作台窗口配对、页面不可见时的处理）。
- 清单新增可选字段：系统标识色 `color`、界面工具显示名 `title`。

### 变更

- 桥接 SDK 连接参数改为 `agent_client`（工作台窗口 ID），旧参数名 `agent_session` 仍兼容。
