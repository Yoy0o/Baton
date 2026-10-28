"""Baton：通用 Agent 编排多个 Web 系统（名称见 config.Settings.app_name）。

模块划分（与 docs/ARCHITECTURE.md 一致）：
- config         运行配置（环境变量）
- contracts      工具契约：ToolSpec、返回信封、错误
- manifest       接入清单（manifest.yaml）模型、加载与校验
- results        结果仓库：result_id 句柄与格式转换
- providers      三类能力来源：OpenAPI 适配、前端桥接、内置能力
- gateway        能力网关：工具目录、参数校验、风险策略、确认、审计
- llm            模型接入：OpenAI 兼容接口（流式）+ 演示模式脚本规划器
- conversations  会话存储：会话列表、模型历史、界面事件（用于重放）
- runtime        编排循环：流式输出、停止、重新生成、确认等待
- server         HTTP / WebSocket 服务（工作台、桥接、会话、结果、MCP 调试端点）
"""

__version__ = "0.2.0"
