# 2026-10-29 AGENTS.md 覆盖核对与开发留痕初始化

- 执行：Agent（Claude Code，Opus 5.5），应 gary.liu 要求
- 关联：基于提交 14d49de；ADR-0001 至 0009；CHANGELOG“未发布”

## 目标

1. 核对 `AGENTS.md` 的每条说法是否与代码一致、是否覆盖了常见改动；
2. 检查项目是否符合自己声明的约定（代码约定、仓库卫生、文档同步）；
3. 建立开发留痕：CHANGELOG、架构决策记录、开发日志，并在 `AGENTS.md` 写明何时更新哪份文档。

范围只到文档和配置样例。发现的代码问题列在“遗留问题”，没有改代码。

## 改动

- `AGENTS.md`
  - 更正：“不要在别处读 `os.environ`”与现状不符，改为列出三处按设计的例外（清单变量展开、`token_env`、`run_demo.py`）；“pydantic v2 做模型”改为“外部数据用 pydantic，内部状态用 dataclass”（`config.Settings` 等都是 dataclass）；去掉会过时的“40 个测试”；`npm install` 改为 `npm ci`；注明 `baton.*` 存储键和 `io.baton/*` 命名空间不属于“硬编码产品名”。
  - 补充：架构图补 `manifest.py` / `check.py` / `analytics.py` / `_demo_data.py`；环境版本；每条约束链接对应 ADR；“常见改动”补桥接 SDK、REST/WS 接口、演示数据、接入规则、发版五行；新增“完成前检查”“文档与留痕”两节和提交信息约定。
- `docs/ARCHITECTURE.md`：第 5 节补 `AGENT_HTTP_TIMEOUT_S`、`AGENT_DEMO_USER`、`AGENT_TZ`（`config.py` 里已有、文档没写）；`AGENT_PUBLIC_URL` 的说明从“用于 CORS”补全为“下发给内嵌页面 + CORS”（见 `server.py` 的 `allowed_origins` 和 `/api/config`）；第 1 节请求头补 `X-Agent-Session`（`openapi_provider.py` 实际会发）。
- `.env.example`：补 `LLM_TEMPERATURE` 和 6 个 `AGENT_*` 可选项，均为注释、取默认值，不改变运行行为。
- `README.md`：文档入口补 CHANGELOG 和 AGENTS.md，目录说明补 `docs/adr/`、`docs/devlog/`。
- 新建 `CHANGELOG.md`（未发布 + 0.2.0）、`docs/adr/`（索引、模板、ADR-0001 至 0009）、`docs/devlog/`（说明 + 本篇）。
- 没有另建 `CLAUDE.md`：本次会话中 Claude Code 已直接读取 `AGENTS.md`，再建一份只会造成两处维护。

## 验证

| 检查 | 结果 |
| --- | --- |
| `py -m pytest -q`（Python 3.10.6） | 40 passed，5.1 秒 |
| `cd web && npm ci && npm run build -- --outDir <临时目录>`（Node 24.21） | 通过，含 `tsc --noEmit`；产物 4 个文件与已提交的 `web/dist/` 的 MD5 完全一致，说明 dist 与源码同步 |
| `AGENTS.md` 引用的符号 | 均存在：`Gateway.call`、`gateway._dispatch`、`ctx.confirm` 为空时拒绝写入（`gateway.py:301`）、`asyncio.shield`（`gateway.py:312`）、`RouterTransport`、`complete(messages, tools, on_delta=None)`、`run_demo.py --agent-only` |
| 事件协议三方一致 | `runtime.py` 发出的事件类型、`conversations.EPHEMERAL_EVENTS`（不保存的事件）、`store.ts` / `App.tsx` 处理的事件与 ARCHITECTURE 3.5 表一致 |
| 文档引用的章节号 | ARCHITECTURE 3.5 / 第 5 节、INTEGRATION_RULES 第 9 / 10 节均存在且内容对应 |
| 系统知识包 | 3 个 `SKILL.md` 都有 `name` / `description` frontmatter，均不超过 2000 字（R6.2） |
| 仓库卫生 | `data/`、`.env` 未被跟踪；前端依赖均为精确版本 |
| `from __future__ import annotations` | 除 5 个 `__init__.py`（空文件，或只有 docstring 和 `__version__`）外全部具备；AGENTS.md 已写明只要求非空文件 |
| `python -m agent.check` | 没运行（需要先启动三个系统）；清单校验由 `tests/test_manifest.py` 覆盖 |

## 遗留问题

按建议处理顺序排列，都需要先确认再改：

1. **模型模式不读 `AGENT_TZ`**。`agent/runtime.py:374` 的系统提示词固定用 `timezone(timedelta(hours=8))` 取当前时间，只有演示模式（`demo_planner.py:210`）用了 `settings.timezone`。部署在非东八区或改了 `AGENT_TZ` 时，模型看到的“现在”与演示模式不一致。建议复用 `demo_planner` 里基于 `ZoneInfo(settings.timezone)` 的取时逻辑，并补一个测试。
2. **没有 lint / 格式化和 CI**。仓库里没有 `pyproject.toml` / ruff 配置（`.gitignore` 却列了 `.ruff_cache/`），前端没有 eslint / prettier，也没有 CI 和 git remote。“dist 与源码一致”“测试通过”目前全靠人记得跑。建议：先加 ruff（只开 `E`、`F`、`I` 规则）和一个 CI 任务（pytest + `npm run build` + 检查 `web/dist` 无差异）；引入工具链属于“引入依赖”，按 AGENTS.md 先写 ADR。
3. **`run_demo.py` 重复读环境变量**。第 41–54 行用 `os.getenv` 读 `AGENT_PORT`、`AGENT_HOST`、`APP_NAME`、`LLM_*`，默认值与 `config.Settings` 重复，且第 30、54 行硬编码了 “Baton”。建议在 `load_dotenv()` 后改用 `Settings()`；`FLEET_URL` 等清单变量保持现状。
4. **测试夹具只清理了部分环境变量**。`tests/conftest.py` 的 `settings` 夹具清理了 `LLM_*`、系统地址和 `APP_NAME`，没有清理 `AGENT_*`；开发者的终端如果导出了 `AGENT_MAX_STEPS` 等变量，测试结果可能不同。建议改为清理所有 `AGENT_` 前缀的变量。
5. **前端单个 JS 包 1.4 MB（gzip 后 453 kB）**。演示可接受；正式使用时可按页签拆分（工作区各页签、Markdown 渲染）。优先级低。
6. `web/index.html` 的 `<title>Baton</title>` 是静态的，页面加载后由 `App.tsx:476` 按配置改写，只在加载前一瞬可见。可接受，记录在此备查。

## 后续

同日按用户要求处理了上面第 1–4 项，并去掉了 demo / 演示 命名，见 [2026-10-29-normalize-naming-and-tooling.md](2026-10-29-normalize-naming-and-tooling.md)。本篇保留当时的文件名（如 `run_demo.py`、`demo_planner.py`），它们指改名前的文件。
