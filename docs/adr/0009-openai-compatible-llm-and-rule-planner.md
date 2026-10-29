# ADR-0009 模型走 OpenAI 兼容接口；无模型时用规则规划器走同一链路

- 状态：已采纳
- 日期：2026-10-28（2026-10-29 补记）
- 相关：ARCHITECTURE 3.7、`agent/llm.py`、`agent/rule_planner.py`

## 背景

目标用户使用的模型服务各不相同（通义千问、DeepSeek、智谱、Kimi、vLLM、Ollama、OpenAI 等）。同时，联调和测试经常没有模型密钥，但仍需要验证网关、确认、桥接、审计这条链路。

## 决策

- 模型接入只认 OpenAI 兼容的 Chat Completions 接口（需支持 tools / function calling），默认流式，接口不支持时退回一次性返回；返回的 `reasoning_content` 作为思考过程展示。
- `llm.py` 定义的协议只要求 `complete(messages, tools, on_delta=None)`，换模型接口只改这里。
- 未配置 `LLM_BASE_URL` 与 `LLM_MODEL` 时，用 `rule_planner.py` 的规则脚本代替模型（规则模式），按步骤产出与模型相同格式的工具调用，走同一条运行时 → 网关 → provider 链路。

## 考虑过的其他方案

- 为各家模型分别写适配：维护成本高，主流服务都已提供兼容接口。
- 无模型时直接返回预先录制的事件：绕过了网关和确认，验证不了架构。

## 后果

- 好处：不改代码即可切换模型；测试和联调不依赖外部服务，`tests/test_scenarios.py` 用规则模式跑完整场景。
- 代价：规则模式只识别少数几类指令；README“验证清单”里的数字依赖内置样例数据，改样例数据或规划器时要同步核对。
- 后续：无。
