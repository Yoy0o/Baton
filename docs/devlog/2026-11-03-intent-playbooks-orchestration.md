# 2026-11-03 编排重构：意图识别、场景剧本与执行器

- 执行：Agent（Claude Code），应用户要求
- 关联：ADR-0011（提议）；CHANGELOG“未发布”中场景剧本、`/api/intents`、`run_started.intent`、`REPEATED_CALL`、路由与调度各条；提交见 `git log -- docs/devlog/2026-11-03-intent-playbooks-orchestration.md`

## 目标

用户要求：Agent 编排部分与架构需要优化，意图分类器和执行也要优化，方便后续继续扩展对接的系统和功能。

具体要解决的问题（改动前的状态）：

1. 规则模式的意图识别是 `rule_planner.detect_intent` 里一串按顺序排列的正则，每个场景一个手写 `_plan_*` 方法，工具名、参数解析、答复文案都写在 Python 里；接入新系统要改代码。
2. 识别结果只给规则模式用；模型模式拿不到任何提示；工具多时网关只按 `keywords` 子串路由，追问会丢掉上一轮用过的系统。
3. `runtime._run` 把提示词、模型调用、工具调度、事件推送放在一个方法里；工具调度只有“全并行 / 全串行”两档；模型原样重试失败调用只能靠最大步数兜底。

范围：后端编排、剧本格式与校验、工作台常用任务改由服务端提供；不改确认、审计、结果仓库、桥接协议；不放宽任何约束。

## 改动

- 新增 `agent/slots.py`：槽位类型注册表（`choice`、`flag`、`date`、`number`、`duration`、`pattern`、`assignments`），`@extractor` 可扩展，`check_slot` 做参数校验（R11.5）。原 `rule_planner` 里的 `parse_*` 函数都归到这里，变成可配置的通用类型。
- 新增 `agent/intents.py`：剧本文件的 pydantic 模型与 R11.1–R11.6 校验；`IntentLibrary`（加载、可用性、帮助文本、常用任务、`classify` 识别、`warnings`）；`validate_library` 做跨清单检查（意图 id 唯一、工具存在、`call` 不得调用写操作、`write` 的 preview 须声明 `pairs_with`）。
- 新增 `agent/playbook.py`：模板渲染（`{名字.路径|备选|字面量}`）、`PlaybookRunner`（规则模式按剧本推进一步）、`describe`（给模型的“本轮任务识别”文本）。
- 新增 `agent/transcript.py`：消息历史解析（原 `rule_planner._Convo`），增加 `take` 认领语义（同一工具在剧本里出现多次也能对上号）、`recent_systems`（上一轮用过的系统）。
- `agent/rule_planner.py`：只剩“识别 → 剧本推进 → 流式输出”，不含任何具体场景。
- 新增 `systems/dispatch/playbooks.yaml`（regroup / adjust / sync）、`systems/fleet/playbooks.yaml`（stops / track / locate），两个清单加 `playbooks` 字段。原 6 个场景的工具顺序、参数、答复文案保持不变；track 增加 `requires.slot: driver`（没给司机 ID 时先问，不再默认 D0012）。
- `agent/manifest.py`：清单字段 `playbooks`；`load_manifest` 解析并校验剧本，`load_manifests` 追加跨清单校验。
- 拆分 `agent/runtime.py`：新增 `agent/prompt.py`（系统提示词，增加意图一节与“互不依赖的查询可一起调用”）和 `agent/executor.py`（解析调用、按风险分批：相邻只读 / 只改界面调用并行，写操作、未知工具、参数有误的单独执行；`arguments: "null"` 视为空对象）。运行时新增 `Turn`（识别结果、路由偏好、可用工具）和 `_plan_turn`（识别失败时降级为不识别）；`run_started` 带 `intent`。
- `agent/gateway.py`：`select(..., prefer=)` 路由偏好；同一次运行内原样重复不可重试的失败调用返回 `REPEATED_CALL`（`CallContext.failures`，照常写审计；`UI_NOT_CONNECTED` 除外，用户可能在运行中打开页面）。`BUILTIN_SYSTEMS` 移到 `contracts.py`（`intents.py` 要用，避免依赖网关）。
- `agent/server.py`：`Platform` 持有 `IntentLibrary` 并传给规则模式和运行时；`/api/config` 增加 `presets`；新增 `GET /api/intents`。`agent/llm.py` 的 `make_llm` 接收剧本库。
- `agent/check.py`：列出各系统场景；提醒不可用的场景、引用了未生成工具的场景。
- 工作台：新增 `web/src/presets.tsx`（常用任务来自 `config.presets`，附“能做什么”），`Composer.tsx`、`WelcomeScreen.tsx` 改用它，删掉写死的 `TASK_PRESETS`；`api.ts` 增加类型。重建 `web/dist`。
- 测试：新增 `tests/test_intents.py`（槽位、识别排序、`from_call`、帮助与常用任务、模板、R11 规则违例、不可用意图）；`test_gateway.py` 增加重复失败拦截、路由偏好、执行器分批；`test_scenarios.py` 增加缺参数追问与帮助、`run_started.intent`、模型模式提示词内容；`test_server.py` 增加 `presets` 与 `/api/intents`。
- 文档：ADR-0011 与索引；INTEGRATION_RULES 新增第 11 节（R11.1–R11.7）、第 9 节步骤、第 10 节字段；ARCHITECTURE 第 1、2、3.1、3.5、3.7、4、6、7 节；AGENTS.md 架构图与常见改动；README；CHANGELOG。

## 验证

| 检查 | 结果 |
| --- | --- |
| `python -m pytest -q`（Python 3.13.18） | 86 passed（原 42 个全部通过，新增 44 个） |
| `python -m pytest -q`（Python 3.11.x，独立 venv） | 86 passed（1 个来自依赖库的警告，与本次改动无关） |
| `ruff check .` | 通过 |
| `cd web && npm ci && npm run build`（Node 22.23，含 `tsc --noEmit`） | 通过；改动前先确认重建产物与已提交的 `web/dist` 完全一致，改动后再次重建得到相同的内容哈希 `index-B-QSA4t0.js` |
| `python run.py` 后 `python -m agent.check -v` | 3 个系统，0 个错误，0 个提醒；6 个场景都列出 |
| 文档里的剧本示例（INTEGRATION_RULES 第 11 节） | 用 `PlaybookFile` 解析并跑跨清单校验，无问题 |
| 浏览器（Playwright，规则模式） | 新对话页常用任务为剧本生成的 4 项；“挂车停留地图”：34 条、6 个客户、地图着色；“重组司机组”：7 步、确认卡片 36 人换组、确认后“调度系统：已提交”；无控制台错误 |
| `GET /api/intents?text=…` | 返回 regroup 及取出的参数和提示文本 |
| 真实模型 | 没运行（本环境无模型密钥）；模型路径由 `test_openai_compatible_*` 经假的 OpenAI 兼容接口覆盖，并断言提示词含“本轮任务识别” |
| Python 3.10 | 没运行（环境里没有 3.10）；代码未使用 3.11+ 语法 |

## 遗留问题

1. ADR-0011 状态为“提议”，需要人工确认后改为“已采纳”。
2. 识别是纯规则匹配，不理解同义改写（如“重新编排班组”不含任何触发词时识别不到）。模型模式下只影响提示，不影响能力；若要提高命中率，可在剧本里补触发词，或后续评估“规则 + 模型”两级识别（需新 ADR）。
3. 识别命中情况目前只在 `run_started.intent` 里，没有统计。建议后续把“未识别”的提问汇总到审计或单独日志，作为补剧本的依据。
4. 工作台还没有展示 `run_started.intent`（字段已在事件里、会随对话保存）。可以在执行过程头部显示“识别为：重组司机组”。
5. 剧本模板的条件只支持取真 / `not`，复杂分支仍需要模型；这是有意的简化，若接入方确有需要，再扩展 `when` 的表达式（同步改 R11.4）。
