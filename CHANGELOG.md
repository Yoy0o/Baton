# 更新日志

记录每个版本对用户、接入方和协议的变化；内部重构不记。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。每个版本下按“新增 / 变更 / 修复 / 移除”分组，没有的分组省略。

开发过程的细节见 [docs/devlog/](docs/devlog/README.md)，设计理由见 [docs/adr/](docs/adr/README.md)。

## [未发布]

### 新增

- `AGENTS.md`：给编码 Agent 的仓库速查（架构、命令、不能破坏的约束、常见改动位置、完成前检查、文档与留痕规则）。
- 开发留痕：本文件、`docs/adr/`（架构决策记录，补记 v0.2 基线决策 ADR-0001 至 0009）、`docs/devlog/`（开发日志）。
- 代码检查与 CI：`ruff check .`（配置在 `pyproject.toml`）；`.github/workflows/ci.yml` 跑 ruff、pytest、前端构建和 `web/dist` 一致性检查；`.gitattributes` 统一 LF 换行（ADR-0010）。
- Windows 上自动安装 `tzdata`，保证 `AGENT_TZ` 可识别。

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

### 修复

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
