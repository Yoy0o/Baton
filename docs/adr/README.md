# 架构决策记录（ADR）

记录“为什么这样设计”：每篇写一个决策的背景、取舍和后果。代码和 ARCHITECTURE 说明“现在是什么样”，ADR 说明“当初为什么选它、放弃了什么”，便于后来的人（和编码 Agent）在改动约束前先知道代价。

## 什么时候写

- 新增、放宽或替换 [AGENTS.md](../../AGENTS.md)“不能破坏的约束”中的任何一条；
- 引入新的依赖、存储、进程、协议，或改变已有协议的语义（不是加个可选字段）；
- 在两个以上可行方案之间做了取舍，且以后很可能有人问“为什么不用另一个”。

小改动、纯重构、修 bug 不写 ADR，记在 [CHANGELOG](../../CHANGELOG.md) 或[开发日志](../devlog/README.md)里即可。

## 怎么写

1. 复制 [template.md](template.md)，按下一个编号命名：`NNNN-英文短横线标题.md`；
2. 先以“提议”状态提交，讨论定下后改为“已采纳”；
3. 已采纳的 ADR **不改正文**。要推翻时写一篇新的，在新 ADR 里写“取代 ADR-NNNN”，再把旧 ADR 的状态行改为“已被 ADR-MMMM 取代”；
4. 在下面的索引里加一行。

状态取值：提议 / 已采纳 / 已废弃 / 已被 ADR-NNNN 取代。

## 索引

0001–0009 为 v0.2 基线决策，2026-10-29 根据当时的代码和文档补记，决策本身在 v0.2（2026-10-28 首次提交）时已生效。

| 编号 | 决策 | 状态 | 对应约束 |
| --- | --- | --- | --- |
| [0001](0001-record-architecture-decisions.md) | 用 ADR 记录架构决策 | 已采纳 | — |
| [0002](0002-gateway-single-entry.md) | 所有工具调用经能力网关，错误一律返回信封 | 已采纳 | AGENTS 约束 1 |
| [0003](0003-two-phase-write.md) | 写操作两段式：预览 → 用户确认 → 提交 | 已采纳 | AGENTS 约束 2 |
| [0004](0004-result-references.md) | 大数据走 `result_id` 引用，模型只看摘要和预览 | 已采纳 | AGENTS 约束 3 |
| [0005](0005-ui-tools-view-read-only.md) | 界面工具只能是 `view` / `read` | 已采纳 | AGENTS 约束 4 |
| [0006](0006-wgs84-coordinates.md) | 平台坐标统一 WGS84 | 已采纳 | AGENTS 约束 5 |
| [0007](0007-event-sourced-workbench.md) | 事件即真相：工作台单一 reducer，实时与重放同一逻辑 | 已采纳 | AGENTS 约束 6 |
| [0008](0008-commit-web-dist.md) | 工作台构建产物 `web/dist` 随代码提交 | 已采纳 | 仓库卫生 |
| [0009](0009-openai-compatible-llm-and-rule-planner.md) | 模型走 OpenAI 兼容接口；无模型时用规则规划器走同一链路 | 已采纳 | — |
| [0010](0010-lint-ci-and-line-endings.md) | 引入 ruff 检查、CI 与统一 LF 换行 | 已采纳 | 完成前检查 |
