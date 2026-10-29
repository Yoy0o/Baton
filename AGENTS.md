# AGENTS.md

给在本仓库工作的编码 Agent 看的速查。产品说明见 [README.md](README.md)，完整架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，系统接入规则见 [docs/INTEGRATION_RULES.md](docs/INTEGRATION_RULES.md)；这里不重复，只写动手前必须知道的事。

## 项目是什么

Baton：通用 Agent 通过**能力网关**调度多个操作类 Web 系统。Agent 只做编排，所有系统能力统一成工具；后端零改动接入（OpenAPI + 接入清单），界面能力用桥接 SDK 暴露，写操作必须“预览 → 用户确认 → 提交”。

## 架构一图

```
web/ 工作台 (React + Ant Design X)      ← WS /ws/workbench、REST /api/*
   └─ iframe: 业务系统页面 + sdk/agent-bridge.js  ← WS /ws/bridge
agent/ Agent 服务 (FastAPI :8000)
   server.py  HTTP/WS 入口          runtime.py  编排循环、停止/重新生成、确认等待、事件推送
   gateway.py 目录·校验·风险·confirm_token·审计     conversations.py 对话 JSON 与事件重放
   providers/ openapi（API）· bridge（界面）· builtin（分组/画布/结果）
   results.py 结果仓库(result_id)   audit.py 审计   llm.py / demo_planner.py 模型 / 演示规划
systems/  三个模拟业务系统 fleet:8101 · tms:8102 · dispatch:8103，各带 manifest.yaml + SKILL.md
```

数据流要点：模型 → 运行时 → `Gateway.call()` → provider；工具结果入结果仓库，模型只拿 `result_id`、摘要和 ≤5 条预览。

## 常用命令

```bash
pip install -r requirements.txt -r requirements-dev.txt
python run_demo.py                  # 起三个模拟系统 + Agent，http://localhost:8000；--agent-only 只起 Agent
python -m pytest -q                 # 40 个测试，不占端口（ASGI transport），不依赖前端构建
python -m agent.check [manifest.yaml] -v   # 校验接入清单，需系统已启动
cd web && npm install && npm run typecheck # 前端类型检查；npm run build 生成 web/dist；npm run dev 联调（代理到 8000）
```

Windows 下 `python` 可能是 Store 占位符，用 `py -m pytest -q`。未配置 `LLM_*` 即为演示模式（`demo_planner.py` 规则脚本，走同一条链路）。

## 不能破坏的约束

1. **所有工具调用经 `Gateway.call()`**。不要在运行时、server 或 provider 里绕过网关直连业务系统；网关不抛异常，一律返回带 `fix_hint` 的错误信封（`contracts.py`）。
2. **写操作两段式**。提交工具只收 `confirm_token`，令牌由网关在预览成功后签发、绑定对话、10 分钟有效，并回放预览时保存的参数；确认只能由运行时的 `ctx.confirm` 通道产生。`/mcp` 与 `/api/ui-actions` 没有确认通道，不得放开写入。用户确认后的提交用 `asyncio.shield` 保护，不被“停止”打断。
3. **大数据走引用**。不要把整份列表放进模型上下文；新增参数需要接收上一步结果时用清单 `accepts_result`，转换在网关做。
4. **界面工具只能是 `view` / `read`**，业务写入只走 API 预览/提交。工具必须在清单 `ui_bridge.tools` 白名单中。
5. **坐标统一 WGS84**，GeoJSON 顺序 [经度, 纬度]；系统内部的其他坐标系在钩子或系统接口内转换。
6. **事件即真相**。工作台只有一套 reducer（`web/src/store.ts`），实时事件和历史重放共用；需要重放的事件带递增 `seq` 并落盘。新增事件类型时同时改 `runtime.py`/`conversations.py`、`store.ts`，并更新 ARCHITECTURE 3.5 的事件表。
7. **协议字段同步**：改动 WS 消息、信封、清单字段时，同步更新 `docs/` 对应章节；规则编号（R1.1 等）与代码校验提示一一对应，别改号。

## 常见改动怎么做

| 任务 | 改哪里 |
| --- | --- |
| 接入新系统 | 新建 `systems/<name>/manifest.yaml`（+ `SKILL.md`、可选 `adapter_hooks.py`），按 INTEGRATION_RULES 第 9 节；用 `agent.check` 验证，补一条 `tests/test_scenarios.py` 风格的场景测试 |
| 新增能力来源 | `agent/providers/` 实现 `specs()` 与 `call()`，在 `gateway._dispatch` 按 `kind` 分发 |
| 新增内置工具 | `providers/builtin_provider.py` |
| 换模型接口 | `llm.py`，只需满足 `complete(messages, tools, on_delta=None)` |
| 新增配置项 | `agent/config.py` 的 `Settings` + `.env.example` + ARCHITECTURE 第 5 节 |
| 工作台界面 | `web/src/components/`；视觉约定在 `web/src/theme.ts`（琥珀色只表示写操作/确认，系统标识色避开琥珀、绿、红） |

## 代码约定

- Python ≥ 3.10，文件顶部 `from __future__ import annotations`；pydantic v2 做模型，httpx 做 HTTP，全程 async。
- 注释和 docstring 用中文，说明“为什么”；面向用户的文案也是中文。保持这个风格和现有的注释密度。
- 配置只从环境变量读（`config.py`），不要在别处读 `os.environ`；产品名只来自 `APP_NAME`，不要硬编码 “Baton” 到界面或服务名里。
- 前端依赖版本是精确锁定的（无 `^`），升级需要同时更新 `package-lock.json`。
- 测试用 `tests/conftest.py` 的 `RouterTransport` 把请求路由到各系统 app，不要在测试里起真实端口；时间相关设置在夹具里调小。

## 仓库卫生

- `data/`（对话、结果、审计）和 `.env` 被 git 忽略，不要提交。
- **`web/dist/` 是随代码提交的构建产物**（部署机不需要 Node）。改了 `web/src/` 后要重新 `npm run build` 并一并提交 dist，否则运行的页面和源码不一致。
- 单用户演示版：未接 SSO，系统鉴权只有 `none` / `static_bearer`；确认令牌在进程内存里，重启后作废。边界清单见 README 末尾，别在没讨论的情况下把这些当作已实现。
