# ADR-0010 引入 ruff 检查、CI 与统一 LF 换行

- 状态：已采纳
- 日期：2026-10-29
- 相关：AGENTS.md“完成前检查”、ADR-0008、`pyproject.toml`、`.github/workflows/ci.yml`、`.gitattributes`

## 背景

2026-10-29 的核对（`docs/devlog/2026-10-29-agents-audit.md`）发现：仓库没有任何代码检查和 CI，“测试通过”“`web/dist` 与源码一致”全靠人记得跑；`.gitignore` 列了 `.ruff_cache/` 但没有 ruff 配置。开发在 Windows 上进行（`core.autocrlf=true`），而 dist 一致性检查要求 Windows 与 Linux 构建出相同的哈希。

## 决策

- **Python 检查用 ruff**，版本精确锁定在 `requirements-dev.txt`，配置放 `pyproject.toml`。规则选 `E` / `W` / `F` / `I` / `UP` / `B`，不检查行宽（`E501`）。只做检查，不引入 `ruff format`：现有代码风格一致但不是 formatter 的风格，整体格式化会产生大量无意义差异。
- **CI 用 GitHub Actions**（`.github/workflows/ci.yml`）：Python 3.10 与 3.13 上跑 `ruff check` 和 pytest；Node 22 上 `npm ci && npm run build`，再检查 `web/dist` 没有差异。仓库目前没有远端，推送到 GitHub 后自动生效。
- **换行统一 LF**（`.gitattributes` 的 `* text=auto eol=lf`）：Windows 检出也是 LF，本地构建与 CI 构建的产物一致。仓库里已有文件本来就是 LF，加规则不产生改动。
- 前端暂不引入 eslint / prettier：`tsc` 严格模式已在构建时运行，再加工具链要多维护一套依赖和规则，等有具体问题再议。

## 考虑过的其他方案

- flake8 + isort + pyupgrade：三个工具三份配置，ruff 一个工具覆盖且更快。
- pre-commit 钩子：依赖每台机器单独安装，作为 CI 的补充可以后加，不能代替 CI。
- 在 CI 里构建并上传 dist、仓库不再提交 dist：需要制品库和部署流程配合，超出本次范围，见 ADR-0008。

## 后果

- 好处：约束从“记得跑”变成自动检查；dist 不同步会在合并前暴露。
- 代价：开发依赖多一个 ruff；升级 ruff 版本可能带来新的检查项，要一并修。
- 后续：配置远端仓库后确认 CI 首次运行通过；前端 lint 待议。
