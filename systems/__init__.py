"""模拟业务系统（开发、联调与测试用）。每个系统是独立的 FastAPI 服务，自带 OpenAPI 文档，代表“现有系统”。

接入 Agent 只靠各目录下的 manifest.yaml（接入清单）、SKILL.md（知识包）和可选的 adapter_hooks.py。
"""
