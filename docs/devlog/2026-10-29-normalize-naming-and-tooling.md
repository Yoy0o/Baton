# 2026-10-29 去掉 demo 命名、补代码检查与 CI、修复时区与测试隔离

- 执行：Agent（Claude Code，Opus 5.5），应 gary.liu 要求
- 关联：[2026-10-29-agents-audit.md](2026-10-29-agents-audit.md) 遗留问题 1–4；ADR-0010；CHANGELOG“未发布”的“变更”“修复”

## 目标

1. 处理上一篇核对留下的四个问题：模型模式不读 `AGENT_TZ`、没有代码检查和 CI、`run_demo.py` 重复读环境变量、测试夹具环境隔离不完整；
2. 文件名和文件内容去掉 demo / 演示 字样，规范工程命名。

## 改动

**命名**（文件用 `git mv` 改名，保留历史）

| 原名 | 新名 | 说明 |
| --- | --- | --- |
| `run_demo.py` | `run.py` | 启动器 |
| `agent/demo_planner.py`、`DemoPlanner` | `agent/rule_planner.py`、`RulePlanner` | 无模型时的规则规划器，模式名“规则模式” |
| `systems/_demo_data.py`（导入别名 `demo`） | `systems/_sample_data.py`（别名 `sample`） | 模拟系统共用的样例数据 |
| `DEMO_STREAM_DELAY_S`、`demo_stream_delay_s` | `RULE_STREAM_DELAY_S`、`rule_stream_delay_s` | |
| `AGENT_DEMO_USER`、`demo_user`，默认 `demo` | `AGENT_DEFAULT_USER`、`default_user`，默认 `local` | |
| `/api/config` 的 `mode: "demo"` | `mode: "rule"` | 前端 `api.ts`、`SystemsButton.tsx` 同步 |
| 测试里的用户名 `"demo"`、清单样例 `system: demo` | `"local"`、`system: sample` | |
| ADR-0009 文件名 `…-demo-planner.md` | `…-rule-planner.md` | 尚未提交，直接改名 |

中文“演示模式 / 演示数据 / 演示用 / 单用户演示”分别改为“规则模式 / 样例数据 / 联调与测试用 / 单用户”，覆盖代码注释、界面文案、清单注释、README、ARCHITECTURE、INTEGRATION_RULES（R8.1 的说明文字，编号不变）、AGENTS.md 和 ADR。“模拟业务系统”保持不变，它说的是这些系统的性质。

**四个问题**

1. 时区：`config.Settings` 新增 `tzinfo` 属性（`ZoneInfo(AGENT_TZ)`，识别不了时退回东八区并记警告）；`runtime._system_prompt` 和 `RulePlanner` 都改用它，提示词里写出时区名和 UTC 偏移。删掉了规划器里自己的 `_today` 时区逻辑。`requirements.txt` 在 Windows 上加 `tzdata`。
2. 代码检查与 CI：新增 `pyproject.toml`（ruff 规则 E/W/F/I/UP/B，忽略 E501）、`requirements-dev.txt` 锁定 `ruff==0.14.4`、`.github/workflows/ci.yml`、`.gitattributes`（统一 LF），理由见 ADR-0010。ruff 首次运行只有 6 处：1 处导入顺序（自动修复），5 处 `raise` 缺少 `from`（B904，在 `bridge_provider.py`、`openapi_provider.py`、`runtime.py`、`server.py` 补上 `from None` / `from e`，只影响异常链，不改行为）。
3. `run.py`：端口、监听地址、产品名、模型配置、前端目录都改从 `Settings` 读取；只有清单的系统地址变量（`FLEET_URL` 等）按清单方式直接读，已在注释和 AGENTS.md 中说明。
4. 测试夹具：`settings` 夹具清掉所有 `APP_` / `LLM_` / `AGENT_` / `RULE_` 前缀和清单地址变量。

**顺带修复**：`python -m agent.check` 在 Windows 中文控制台（GBK）打印 ✓ 时抛 `UnicodeEncodeError` 直接退出（改动前就存在，本次运行时发现）。`check.main` 里把标准输出改为 UTF-8、编不了的字符替换。

**测试**：新增 `tests/test_config.py`（`tzinfo` 跟随 `AGENT_TZ`、未知时区退回东八区）；`test_openai_compatible_non_streaming` 设 `Asia/Tokyo`，断言模型收到的系统提示词含 `Asia/Tokyo，UTC+0900`。

**本地环境**（不进仓库）

- `.env` 里的 `DEMO_STREAM_DELAY_S` 改名为 `RULE_STREAM_DELAY_S`，值不变；
- `data/conversations/` 下 2 个已有对话的 `"user": "demo"` 改为 `"local"`，否则改了默认用户后看不到它们。改前备份在本次会话的临时目录（scratchpad 下的 `conversations-backup`）。

**文档**：AGENTS.md 补 ruff 命令和检查项、时间与命名约定、LF 约定；CHANGELOG 记录改名对照表和升级方法；上一篇日志末尾补“后续”指向本篇。

## 验证

| 检查 | 结果 |
| --- | --- |
| `py -m ruff check .` | All checks passed |
| `py -m pytest -q` | 42 passed（原 40 个 + 新增 2 个） |
| `npm run build`（含 `tsc`） | 通过；`index.html` 和 JS 包按源码变化更新（`index-roVBophR.js` → `index-C3raUHdV.js`），CSS 未变 |
| `py run.py` 实际启动 | 4 个服务都起来了；`/api/config` 返回 `load_errors: {}`；迁移后的 2 个对话都能列出。本机 `.env` 配了模型，所以 `mode` 是 `llm`，规则模式的 `mode: "rule"` 由 `tests/test_server.py` 覆盖 |
| `py -m agent.check`（系统运行中） | 3 个系统，0 个错误，0 个提醒（修复编码问题后） |
| 残留检查 | `git grep -i "demo\|演示"` 只剩 CHANGELOG 改名对照表、两篇开发日志里的历史记录，AGENTS.md 里“命名不带 demo”这条约定本身，以及 `web/package-lock.json`、`web/dist` 打包进来的第三方库代码里自带的字样 |
| `.gitattributes` 重新规范化 | 仓库原有文件本来就是 LF，没有产生额外改动 |
| CI | 没运行：仓库还没有远端 |

## 遗留问题

1. 配置远端仓库并推送后，确认 CI 首次运行通过（尤其是 Linux 上 `web/dist` 一致性检查）。
2. 前端 JS 包 1.4 MB，可按页签拆分，优先级低（同上一篇第 5 项）。
3. 前端 lint（eslint / prettier）暂不引入，见 ADR-0010。
