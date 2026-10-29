# AGENTS.md

给在本仓库工作的编码 Agent 看的速查。产品说明见 [README.md](README.md)，完整架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，系统接入规则见 [docs/INTEGRATION_RULES.md](docs/INTEGRATION_RULES.md)，设计理由见 [docs/adr/](docs/adr/README.md)；这里不重复，只写动手前必须知道的事。任务结束前按“完成前检查”和“文档与留痕”两节收尾。

## 项目是什么

Baton：通用 Agent 通过**能力网关**调度多个操作类 Web 系统。Agent 只做编排，所有系统能力统一成工具；后端零改动接入（OpenAPI + 接入清单），界面能力用桥接 SDK 暴露，写操作必须“预览 → 用户确认 → 提交”。当前版本 v0.2，单用户版。

## 架构一图

```
web/ 工作台 (React + Ant Design X)      ← WS /ws/workbench、REST /api/*
   └─ iframe: 业务系统页面 + sdk/agent-bridge.js  ← WS /ws/bridge
agent/ Agent 服务 (FastAPI :8000)
   server.py  HTTP/WS 入口          runtime.py  编排循环、停止/重新生成、确认等待、事件推送
   gateway.py 目录·校验·风险·confirm_token·审计     conversations.py 对话 JSON 与事件重放
   providers/ openapi（API）· bridge（界面）· builtin（分组/画布/结果）
   results.py 结果仓库(result_id)   audit.py 审计   llm.py / rule_planner.py 模型 / 规则规划
   manifest.py 清单模型与规则校验   check.py 接入检查命令   analytics.py 带人数上限的空间分组
systems/  三个模拟业务系统 fleet:8101 · tms:8102 · dispatch:8103，各带 manifest.yaml + SKILL.md；样例数据在 _sample_data.py
```

数据流要点：模型 → 运行时 → `Gateway.call()` → provider；工具结果入结果仓库，模型只拿 `result_id`、摘要和 ≤5 条预览。

## 常用命令

```bash
pip install -r requirements.txt -r requirements-dev.txt
python run.py                       # 起三个模拟系统 + Agent，http://localhost:8000；--agent-only 只起 Agent
python -m pytest -q                 # 全部测试，不占端口（ASGI transport），不依赖前端构建
ruff check .                        # 代码检查（配置在 pyproject.toml）；ruff check . --fix 自动修复导入顺序等
python -m agent.check [manifest.yaml] -v   # 校验接入清单，需系统已启动
cd web && npm ci && npm run typecheck      # 前端类型检查；npm run build 生成 web/dist；npm run dev 联调（代理到 8000）
```

- 环境：Python ≥ 3.10；Node.js 20.19 / 22.12 及以上（只有改前端时需要）。
- Windows 下 `python` 可能是 Store 占位符，用 `py -m pytest -q`、`py -m ruff check .`。
- 未配置 `LLM_*` 即为规则模式（`rule_planner.py` 规则脚本，走同一条链路）。

## 不能破坏的约束

每条的理由和放弃的方案见对应 ADR。确实需要放宽时，先写新的 ADR 讨论，不要在实现里顺手改。

1. **所有工具调用经 `Gateway.call()`**（[ADR-0002](docs/adr/0002-gateway-single-entry.md)）。不要在运行时、server 或 provider 里绕过网关直连业务系统；网关不抛异常，一律返回带 `fix_hint` 的错误信封（`contracts.py`）。
2. **写操作两段式**（[ADR-0003](docs/adr/0003-two-phase-write.md)）。提交工具只收 `confirm_token`，令牌由网关在预览成功后签发、绑定对话、10 分钟有效，并回放预览时保存的参数；确认只能由运行时的 `ctx.confirm` 通道产生。`/mcp` 与 `/api/ui-actions` 没有确认通道，不得放开写入。用户确认后的提交用 `asyncio.shield` 保护，不被“停止”打断。
3. **大数据走引用**（[ADR-0004](docs/adr/0004-result-references.md)）。不要把整份列表放进模型上下文；新增参数需要接收上一步结果时用清单 `accepts_result`，转换在网关做。
4. **界面工具只能是 `view` / `read`**（[ADR-0005](docs/adr/0005-ui-tools-view-read-only.md)），业务写入只走 API 预览/提交。工具必须在清单 `ui_bridge.tools` 白名单中。
5. **坐标统一 WGS84**（[ADR-0006](docs/adr/0006-wgs84-coordinates.md)），GeoJSON 顺序 [经度, 纬度]；系统内部的其他坐标系在钩子或系统接口内转换。
6. **事件即真相**（[ADR-0007](docs/adr/0007-event-sourced-workbench.md)）。工作台只有一套 reducer（`web/src/store.ts`），实时事件和历史重放共用；需要重放的事件带递增 `seq` 并落盘。新增事件类型时同时改 `runtime.py`/`conversations.py`、`store.ts`，并更新 ARCHITECTURE 3.5 的事件表。
7. **协议字段同步**：改动 WS 消息、信封、清单字段时，同步更新 `docs/` 对应章节；规则编号（R1.1 等）与代码校验提示一一对应，别改号，新规则追加新编号。

## 常见改动怎么做

| 任务 | 改哪里 |
| --- | --- |
| 接入新系统 | 新建 `systems/<name>/manifest.yaml`（+ `SKILL.md`、可选 `adapter_hooks.py`），按 INTEGRATION_RULES 第 9 节；用 `agent.check` 验证，补一条 `tests/test_scenarios.py` 风格的场景测试 |
| 改接入规则 / 清单字段 | `agent/manifest.py`（校验提示带规则编号）+ INTEGRATION_RULES 对应规则和第 10 节字段参考 + `tests/test_manifest.py` |
| 新增能力来源 | `agent/providers/` 实现 `specs()` 与 `call()`，在 `gateway._dispatch` 按 `kind` 分发 |
| 新增内置工具 | `providers/builtin_provider.py` |
| 改桥接 SDK / 桥接协议 | `sdk/agent-bridge.js`（纯 JS，无需构建）+ `providers/bridge_provider.py`；参考实现是 `systems/fleet/static/map.html` 末尾；同步 ARCHITECTURE 3.4 与 INTEGRATION_RULES 第 5 节 |
| 新增 / 改 REST 或 WS 接口 | `agent/server.py`；前端调用在 `web/src/api.ts`、`web/src/socket.ts`；同步 ARCHITECTURE 第 1 节和 3.5 节 |
| 换模型接口 | `llm.py`，只需满足 `complete(messages, tools, on_delta=None)` |
| 改样例数据或规则规划器 | `systems/_sample_data.py`、`agent/rule_planner.py`；README“验证清单”里的数字和 `tests/test_scenarios.py` 依赖它们，一并核对 |
| 新增配置项 | `agent/config.py` 的 `Settings` + `.env.example` + ARCHITECTURE 第 5 节 |
| 工作台界面 | `web/src/components/`；视觉约定在 `web/src/theme.ts`（琥珀色只表示写操作/确认，系统标识色避开琥珀、绿、红）；改完重建 `web/dist` |
| 发版 / 改版本号 | `agent/__init__.py` 的 `__version__`、`web/package.json` 与 `package-lock.json`、`sdk/agent-bridge.js` 头注释、README / ARCHITECTURE 标题；CHANGELOG 把“未发布”落成版本号和日期 |

## 代码约定

- Python ≥ 3.10，每个非空 `.py` 文件顶部 `from __future__ import annotations`；pydantic v2 用于需要校验的外部数据（清单、工具契约、请求体），内部状态用 `dataclass`；httpx 做 HTTP，全程 async。
- 注释和 docstring 用中文，说明“为什么”；面向用户的文案也是中文。保持这个风格和现有的注释密度。
- 配置只从 `config.py` 的 `Settings` 读，不要在别处新增 `os.environ` / `os.getenv`。现有的按设计例外只有三处：`manifest.py` 展开清单里的 `${VAR:-默认值}`、`openapi_provider.py` 按清单 `token_env` 读令牌、启动器 `run.py` 读清单的系统地址变量。
- 产品名只来自 `APP_NAME`，不要把 “Baton” 硬编码到界面文案或服务名里。`baton.*` 本地存储键和 MCP `_meta` 的 `io.baton/*` 命名空间是存储 / 协议标识，不随产品名变，也不要改。
- 前端依赖版本是精确锁定的（无 `^`），升级需要同时更新 `package-lock.json`；安装用 `npm ci`。
- 测试用 `tests/conftest.py` 的 `RouterTransport` 把请求路由到各系统 app，不要在测试里起真实端口；时间相关设置在夹具里调小。`settings` 夹具会清掉所有 `APP_` / `LLM_` / `AGENT_` / `RULE_` 前缀的环境变量，新增配置前缀时同步改它。
- 当前时间一律用 `datetime.now(settings.tzinfo)`，不要写死时区。
- 命名不带 demo / 演示 字样：无模型时的规划器叫“规则模式”（`rule_planner.py`），模拟系统的数据叫“样例数据”（`_sample_data.py`）。
- 文本文件统一 LF（`.gitattributes`），不要提交 CRLF。

## 完成前检查

按改动范围逐项跑，结果写进开发日志的“验证”一节（没跑的写明没跑）：

- [ ] `py -m pytest -q` 全部通过、`ruff check .` 无报错（任何 Python 改动）；
- [ ] 改了 `web/src/` 或前端依赖：`npm run build`（含类型检查），把 `web/dist/` 一并提交；
- [ ] 改了清单：系统启动后 `python -m agent.check <manifest> -v` 0 个错误；
- [ ] 改了协议、信封、清单字段、配置项：对应 docs 章节已同步（见下一节）；
- [ ] 改了约束或做了架构取舍：已有 ADR；
- [ ] 用户、接入方或协议能感知到的变化：CHANGELOG“未发布”已记一条。

以上 Python 检查和 dist 一致性检查也由 `.github/workflows/ci.yml` 自动执行（推送到 GitHub 后生效），但不能代替本地运行。

## 文档与留痕

| 发生了什么 | 写到哪里 |
| --- | --- |
| 一次开发任务（Agent 完成的每次都写） | `docs/devlog/YYYY-MM-DD-<主题>.md`：目标、改动、验证、遗留问题，格式见 [docs/devlog/README.md](docs/devlog/README.md) |
| 用户 / 接入方 / 协议可感知的新增、变更、修复、移除 | [CHANGELOG.md](CHANGELOG.md) 的“未发布” |
| 新增、放宽或替换约束；引入依赖、存储、协议；方案取舍 | `docs/adr/` 新增一篇（[模板](docs/adr/template.md)），更新 ADR 索引 |
| 组件、请求链路、事件协议、配置项 | ARCHITECTURE 对应章节（配置是第 5 节，事件是 3.5 节） |
| 接入规则、清单字段 | INTEGRATION_RULES |
| 启动方式、验证清单、版本边界 | README |
| Agent 的工作约定本身 | 本文件 |

规则：已发布版本的 CHANGELOG 条目和已采纳 ADR 的正文不改，只追加；开发日志只追加，发现写错在文末补“更正”。

提交信息：标题一句话祈使句（现有历史为英文，沿用），正文写为什么；关联的 ADR 编号和开发日志路径写在正文末尾。一个提交只做一件事，构建产物和对应源码放在同一个提交里。

## 仓库卫生

- `data/`（对话、结果、审计）和 `.env` 被 git 忽略，不要提交。
- **`web/dist/` 是随代码提交的构建产物**（部署机不需要 Node，[ADR-0008](docs/adr/0008-commit-web-dist.md)）。改了 `web/src/` 后要重新 `npm run build` 并一并提交 dist，否则运行的页面和源码不一致；测试不覆盖这一点。
- 单用户版：未接 SSO（所有请求归属 `AGENT_DEFAULT_USER`），系统鉴权只有 `none` / `static_bearer`；确认令牌在进程内存里，重启后作废。边界清单见 README 末尾，别在没讨论的情况下把这些当作已实现。
