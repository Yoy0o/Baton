# Baton v0.2 · 多系统编排台

一个通用 Agent，通过“能力网关”调度多个偏操作类的 Web 系统（类 GIS）。名字取自指挥棒和接力棒：Agent 不替代任何系统，只负责把一个任务在几个系统之间指挥、传递下去。

- **系统零改动接入后端**：从现有 OpenAPI 文档按接入清单生成工具；
- **界面能力用一个 SDK 暴露**：地图页面把已有的“叠加图层、定位、轨迹回放”注册成工具；
- **写操作两段式**：先预览，用户在确认卡片上点确认后才提交；
- **大数据走引用**：工具之间只传 `result_id`，计算交给工具；
- **全程可追溯**：每一步工具调用都显示在对话里，并写入审计日志。

v0.2 把工作台重做成常规的 Agent 工作台（React + Ant Design X）：左侧对话列表，中间对话，右侧工作区（业务系统页面、结果、工具、审计）。对话会保存，刷新、断线重连、服务重启后都能如实还原。

产品名可在 `.env` 里用 `APP_NAME` 修改（标题、侧栏、服务名都从这里读取），不需要改代码。

架构说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，接入规则见 [docs/INTEGRATION_RULES.md](docs/INTEGRATION_RULES.md)，版本变化见 [CHANGELOG.md](CHANGELOG.md)，后续方向与优先级见 [docs/ROADMAP.md](docs/ROADMAP.md)。参与开发（包括用编码 Agent）先读 [AGENTS.md](AGENTS.md)。

## 快速开始

需要 Python 3.10 及以上（在 3.13 上测试）；构建工作台需要 Node.js 20.19 或 22.12 及以上（在 22 上测试）。

```bash
pip install -r requirements.txt
cd web && npm install && npm run build && cd ..   # 构建工作台前端（发布包里已带构建好的 web/dist，可跳过）
python run.py
```

浏览器打开 <http://localhost:8000>。右侧工作区“车队地图”页签上的圆点变成实心，说明地图页面已接入，可以开始。

**规则模式**：没有配置模型时，Agent 按各系统的场景剧本（`playbooks.yaml`）识别意图并执行，代替模型规划，只认识剧本里声明的几类指令（下面验证清单里的都在），但走的是与真实模型完全相同的链路，答复也按字逐段推送。接入模型后，识别结果作为参考写进系统提示词，模型仍可自由规划。
**接入真实模型**：复制 `.env.example` 为 `.env`，填写任意 OpenAI 兼容接口（需支持 tools / function calling，如通义千问、DeepSeek、智谱、Kimi、vLLM、Ollama 或 OpenAI），重启后可自由提问。默认使用流式输出，模型返回的思考过程（`reasoning_content`）会显示在答复上方，可展开。

```bash
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
LLM_API_KEY=sk-...
```

**换了模型先评测**：系统启动后运行 `python -m agent.evaluate`。它把各系统场景剧本的示例分别交给规则模式和配置的模型，对照工具调用（该调的有没有调、有没有走到写操作确认、多用了几轮），逐个场景给出通过与否。确认卡片默认自动取消，不会修改业务系统；评测用临时数据目录，不影响正式的对话和审计。

**前端开发**：在 `.env` 里加 `AGENT_ALLOWED_ORIGINS=http://localhost:5173`（Agent 服务只接受允许列表里的浏览器来源），`python run.py` 启动后端，另开终端 `cd web && npm run dev`，打开 Vite 给出的地址（接口、桥接 SDK、WebSocket 都代理到 8000 端口）。

**换地址访问**：通过 `http://localhost:8000` 以外的地址（局域网 IP、域名、反向代理）打开工作台时，把 `AGENT_PUBLIC_URL` 改成该地址；否则工作台会一直显示断线，Agent 服务日志里有被拒绝的来源。

## 工作台

| 区域 | 内容 | 常用操作 |
| --- | --- | --- |
| 左侧对话列表 | 按今天、昨天、近 7 天分组；执行中显示转圈，有写操作待确认显示“待确认” | 新对话；重命名、删除；超过 6 个对话时可搜索 |
| 中间对话 | 每次回复包含：识别出的任务（命中场景剧本时）、思考过程（模型模式）、执行过程、确认卡片、答复 | 回车发送，Shift + 回车换行；输入 `/` 选择常用任务；执行中可随时停止 |
| 执行过程 | 每次工具调用是一个“站点”，颜色是所属系统的标识色，换了颜色就是换到了另一个系统；完成后折叠成一条线路缩略 | 展开看参数、预览数据、错误修正提示；点结果编号在工作区打开 |
| 确认卡片 | 写操作的变更清单、失效倒计时 | 确认提交 / 取消；确认前不会修改业务系统 |
| 回复下方 | 复制、重新生成（仅最后一条）、有帮助 / 没帮助、用时、token 用量（模型接口给了才有）；任务完成后给出追问建议 | 点追问建议直接发送 |
| 右侧工作区 | 业务系统页面（内嵌，Agent 通过桥接操作它）、结果、工具、审计 | 拖动左边缘调宽；收起后系统页面保持连接 |
| 结果页签 | 本对话产生的全部结果：点位预览（与车队地图同色）、分组统计、数据表 | 在车队地图显示；下载 CSV / GeoJSON；复制编号 |

窄屏（手机、平板竖屏）下对话列表改为抽屉，工作区改为浮层。支持浅色、深色和跟随系统。

## 验证清单

按顺序在工作台输入（新对话页的常用任务、输入 `/` 都可一键填入）。数字基于内置样例数据，首次运行时应一致。

| # | 操作 | 应看到 | 验证的设计点 |
| --- | --- | --- | --- |
| 1 | 把华东区明天早班的司机按当前位置重新分成 5 组，每组不超过 8 人，先给我看地图和名单，确认后同步到调度系统 | 7 次工具调用，执行过程依次经过排班系统、车队地图台、内置计算、调度系统；车队地图按 5 组着色；确认卡片列出 36 人换组；侧栏显示“待确认”，浏览器标题带 (1)；点“确认提交”后调度系统版本 +1 | 跨系统取数、`result_id` 引用、内置计算、回填原系统界面、预览 → 确认 → 提交 |
| 2 | 点答复里的结果编号 | 工作区切到“结果”：38 个点按组着色、分组统计；点“在车队地图显示”，地图页签重新叠加该结果 | 结果仓库、用户直接调用界面工具（审计里记为“你在工作台操作”） |
| 3 | 把 D0012 换到第 3 组，然后同步到调度系统 | 在上一方案基础上重算，组号不变，确认卡片只有 2 人变化；点“取消”后答复“未做任何修改” | 基于已有结果微调、拒绝路径 |
| 4 | 新对话：在地图上显示过去 24 小时停留超过 2 小时的挂车，按客户着色 | 34 条停留记录，按 6 个客户着色叠加到车队地图；答复里有各客户数量 | 固定参数隐藏、内置统计（不让模型心算） |
| 5 | 同一对话继续：回放司机 D0012 今天的轨迹 | 车队地图上播放红色轨迹 | 调用系统原有界面功能、多轮对话 |
| 6 | 新对话发出第 1 条指令，出现确认卡片后点输入框里的停止 | 确认卡片显示“任务已停止，没有提交”，线路上未完成的站点为虚线；再点“重新生成”，替换掉这次回复重新执行 | 停止、重新生成，写操作不会因此被提交 |
| 7 | 出现确认卡片时刷新页面 | 回到同一对话，卡片仍可确认，确认后照常提交 | 会话保存与事件重放 |
| 8 | 出现确认卡片时重启 Agent 服务 | 顶部提示断线并自动重连；这次任务显示“服务重启，这次任务中断了”，卡片显示“服务重启，没有提交”；可以在同一对话继续 | 中断如实记录，不会误提交 |
| 9 | 打开 <http://localhost:8101/map>（不带参数） | 地图台独立运行，右上角显示“独立运行” | 接入对系统无侵入 |
| 10 | 工作区“工具”“审计”页签 | 12 个 API / 内置工具 + 4 个界面工具，带风险等级和给模型看的描述；每次调用的发起方、结果、耗时、是否经确认 | 工具由清单生成、审计 |

其他检查：

```bash
python -m agent.check            # 校验接入清单、读取各系统 OpenAPI、列出将生成的工具（需系统已启动）
python -m agent.evaluate         # 模型对照评测：用场景剧本的示例对照规则模式与配置的模型（需系统已启动、已配置模型）
pip install -r requirements-dev.txt && python -m pytest -q   # 覆盖接入规则、剧本与意图识别、网关、场景、协议、会话
curl -s -G localhost:8000/api/intents --data-urlencode "text=把北京晚班的司机分成 4 组"   # 只识别不执行：命中的场景、取出的参数
curl -s "localhost:8000/api/intents/log?matched=false"       # 识别记录：命中率、没识别到的提问（补剧本的依据）
cd web && npm run typecheck      # 工作台类型检查
curl -s -X POST localhost:8000/mcp -H 'content-type: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'     # 以 MCP 形式查看工具目录
curl -s -X POST localhost:8103/api/_reset                    # 恢复调度系统的初始分组
```

## 目录

```
agent/                Agent 服务
  server.py             HTTP / WebSocket 入口；main.py 为 uvicorn 入口
  runtime.py            编排循环（识别意图 → 选工具 → 调模型 → 执行）、停止与重新生成、确认等待、事件推送
  prompt.py / executor.py   系统提示词 / 工具调度（相邻只读调用并行、写操作单独执行）
  intents.py / slots.py     场景剧本模型、意图识别、剧本校验 / 槽位类型
  playbook.py / transcript.py   剧本执行器（规则模式）与给模型的提示 / 消息历史解析
  conversations.py      对话保存与重放（data/conversations/）
  gateway.py            能力网关：目录、路由、校验、风险策略、confirm_token、重复失败拦截、审计
  providers/            三类能力来源：openapi_provider / bridge_provider / builtin_provider
  results.py            结果仓库与格式转换
  analytics.py          带人数上限的空间分组算法
  manifest.py           接入清单模型与规则校验
  llm.py / rule_planner.py   OpenAI 兼容模型接入（流式）/ 规则模式（按剧本执行）
  check.py              接入检查命令
  evaluate.py           模型对照评测命令
web/                  工作台前端（React + Ant Design X + Vite），构建产物在 web/dist
  src/App.tsx           布局、会话、实时事件
  src/store.ts          由事件重放得到对话视图（实时与历史同一套逻辑）
  src/components/       对话、执行过程、确认卡片、工作区各页签
sdk/agent-bridge.js   前端桥接 SDK（接口对齐 WebMCP）
systems/              三个模拟业务系统，各带 manifest.yaml、SKILL.md；fleet、dispatch 带场景剧本 playbooks.yaml
  fleet/                车队追踪地图台：REST API + 地图页面（页面末尾是接入代码）
  tms/                  排班系统：REST API
  dispatch/             调度系统：REST API（预览 / 提交），adapter_hooks.py 格式映射示例
tests/                测试
docs/                 架构说明、接入规则、路线图（ROADMAP.md）
  adr/                  架构决策记录（为什么这样设计）
  devlog/               开发日志（每次开发任务的改动、验证、遗留问题）
```

## 接入你自己的系统

1. 确认系统有 OpenAPI 文档，要暴露的接口有 `operationId`；
2. 参照 `systems/fleet/manifest.yaml` 新建 `systems/<名称>/manifest.yaml`，列出白名单、重写描述、标风险，可选 `color` 指定系统标识色；
3. 有写操作时提供预览 / 提交成对接口；需要界面能力时参照 `map.html` 末尾引入 SDK；
4. 可选：参照 `systems/fleet/playbooks.yaml` 为常见任务写场景剧本，规则模式就能执行它，模型模式会参考它，工作台常用任务里也会出现；
5. 运行 `python -m agent.check systems/<名称>/manifest.yaml -v`，用 `/api/intents?text=…` 检查识别，再到工作台试跑。

完整规则与字段说明见 [docs/INTEGRATION_RULES.md](docs/INTEGRATION_RULES.md)。

## v0.2 的边界

- 单用户：未接 SSO（所有请求归属 `AGENT_DEFAULT_USER`）；系统鉴权只支持 `none` / `static_bearer`。
- 对话保存在 `data/conversations/`（每个对话一个 JSON 文件）；确认令牌在进程内存中，服务重启后待确认的写操作作废（如实显示为中断）；结果保存 24 小时，过期自动清理；审计文件按大小轮转、不自动删除。
- 不支持编辑已发送的消息、分支对话、附件上传；“重新生成”只针对最后一条回复。
- 界面工具只支持内嵌在工作台中的页面（内嵌模式），只能是只改界面或只读。
- 工具超过 30 个时按关键词、识别出的意图、上一轮用过的系统路由，没有向量检索；没有程序化工具调用、长任务、子 Agent、MCP Apps、自动化兜底。
- `/mcp` 只实现 `initialize`、`tools/list`、`tools/call`，用于调试。
- 分组算法为纯 Python，适合 2000 个点以内；结果页的点位预览为轻量示意图，完整地图交互在业务系统页面里。
- 规则模式只识别场景剧本里声明的指令（规则匹配，不理解同义改写）；意图识别对模型模式只是提示。
