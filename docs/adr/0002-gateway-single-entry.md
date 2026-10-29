# ADR-0002 所有工具调用经能力网关，错误一律返回信封

- 状态：已采纳
- 日期：2026-10-28（2026-10-29 补记）
- 相关：AGENTS 约束 1、ARCHITECTURE 3.1、`agent/gateway.py`、`agent/contracts.py`

## 背景

能力来自三类来源：业务系统的 OpenAPI、页面通过桥接 SDK 注册的界面工具、平台内置计算。调用方也不止一个：运行时（模型发起）、`/api/ui-actions`（用户在工作台直接操作）、`/mcp`（调试）。参数校验、风险判断、确认、审计、结果入库如果分散在各调用方或各来源里，任何一处遗漏都会变成绕过确认或漏记审计的口子。

模型面对的错误也需要统一：异常栈对模型没有意义，它需要知道“哪里错了、怎么改”。

## 决策

- `Gateway.call(ctx, name, args)` 是唯一的工具调用入口。运行时、server、各 provider 都不直连业务系统。
- provider 只负责两件事：`specs()` 给出工具描述，`call()` 执行；网关在 `_dispatch` 里按 `kind`（api / ui / builtin）分发。
- 网关从不向上抛异常：参数不合法、系统报错、界面未连接、令牌无效等，都变成 `contracts.py` 定义的错误信封（`ok: false`、`error_code`、`message`、`fix_hint`、`retryable`）。
- 同一份工具目录可转为 OpenAI `tools`（给模型）和 MCP `tools/list`（给 MCP 客户端）。

## 考虑过的其他方案

- 各 provider 自己做校验和审计：实现重复，新来源容易漏掉确认或审计。
- 网关抛异常、由运行时捕获转文本：每个调用方都要写一遍转换，`/mcp` 与 `/api/ui-actions` 行为会不一致。

## 后果

- 好处：风险策略、确认、审计只有一处实现；模型可以根据 `fix_hint` 自我修正；新增调用方或来源不会绕过治理。
- 代价：网关是热点模块，改动要格外小心；新增来源必须实现 `specs()` / `call()` 并在 `_dispatch` 注册。
- 后续：无。
