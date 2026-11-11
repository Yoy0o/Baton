# ADR-0012 WebSocket 握手和会改状态的 HTTP 请求校验浏览器来源

- 状态：已采纳
- 日期：2026-11-11
- 相关：ADR-0003、AGENTS 约束 2、ARCHITECTURE 3.3 与第 5 节、INTEGRATION_RULES R5.8、ROADMAP P0-1

## 背景

写操作的确认只能由运行时的确认通道产生（ADR-0003），这条通道在工作台一侧就是 `/ws/workbench` 上的 `confirm_decision` 消息。2026-11-11 核对时发现两个 WebSocket 入口都不看握手请求的 `Origin`：

- WebSocket 不受同源策略和 CORS 约束，任何网页都能对 `ws://localhost:8000` 发起连接；
- 单用户版没有登录，连上就等于是这个用户；
- 于是用户浏览器里打开的任意网页都能连上工作台通道，发一条 `user_message` 发起任务，收到 `confirm_request` 后自己回 `confirm_decision`。实测：带 `Origin: http://evil.example` 的连接完成了一次调度系统提交。

HTTP 侧已有 CORS，但 CORS 只决定浏览器是否让页面读响应，不阻止请求发出；`text/plain` 的 POST 不触发预检，`/mcp` 会照常解析并执行（写操作因为没有确认通道被拒，但只读和界面工具会被执行）。`/ws/bridge` 同理：外来页面可以冒充某个系统的页面注册界面工具、接收调用参数。

约束：不能要求用户登录（单用户版的边界不变）；命令行、脚本、测试这些非浏览器客户端要照常可用；本机服务还要考虑 DNS 重绑定（攻击者的域名解析到 127.0.0.1）。

## 决策

在 `agent/server.py` 加一层 ASGI 中间件 `OriginGuard`，放在最外层：

- **拦什么**：所有 WebSocket 握手，以及方法不是 GET / HEAD / OPTIONS 的 HTTP 请求。请求带了 `Origin` 且不在允许列表时拒绝：WebSocket 在握手阶段关闭（浏览器收到 403，连接不会建立），HTTP 返回 403。服务端记一条警告，写明来源和修正办法。
- **允许列表**：`AGENT_PUBLIC_URL` 的源、各系统清单 `base_url` 与 `ui_bridge.embed_url` 的源、新配置项 `AGENT_ALLOWED_ORIGINS`（逗号分隔）。本机地址的 `localhost` 与 `127.0.0.1` 两种写法都算。与 CORS 用同一份列表（`Platform.allowed_origins()`）。
- **不带 `Origin` 的请求放行**：浏览器发起 WebSocket 和跨站 POST 时一定带 `Origin`，不带的是命令行、脚本、测试，跨站伪造对它们不适用。
- **只读请求不拦**：跨站页面读不到响应（CORS），拦了反而让内嵌系统页面读结果多一层配置。

明确不做：不引入登录或令牌（属于 ROADMAP P2-1）；不按 `Host` 判断同源。

## 考虑过的其他方案

- 判断 `Origin` 与 `Host` 是否同源，不维护列表：部署时不用配置，但挡不住 DNS 重绑定（重绑定后 `Origin` 和 `Host` 都是攻击者的域名，恰好同源）。本机服务最常见的攻击就是它，所以用显式列表。
- 握手后先接受连接，再发一条 `error` 消息并关闭（现有“缺少 client 参数”的做法）：用户能看到原因，但工作台的重连逻辑在连接建立时会把退避时间归零，结果是每 0.8 秒重连一次并反复弹错误。握手阶段拒绝则按 0.8 → 8 秒退避，只显示“断线”横幅；原因写在服务端日志里。
- 给工作台页面下发一次性令牌，WebSocket 连接时带上：能同时解决来源问题和将来的身份问题，但令牌要随页面下发、刷新、与内嵌系统页面共享，实际上是登录体系的一部分，留给 P2-1。
- 只在两个 WebSocket 入口里各写一段判断：HTTP 侧的 `text/plain` 提交仍然漏着，以后新增入口也容易忘。中间件一处生效。

## 后果

- 好处：约束 2 重新成立——没有工作台页面里的真人点击，写操作不会被提交；新增的 WebSocket / HTTP 入口自动受保护。
- 代价 / 限制：
  - 通过 `AGENT_PUBLIC_URL` 以外的地址访问工作台（局域网 IP、反向代理域名）时必须配置 `AGENT_PUBLIC_URL` 或 `AGENT_ALLOWED_ORIGINS`，否则工作台一直显示断线。内嵌系统页面本来就靠 `AGENT_PUBLIC_URL` 连接桥接中心，所以这不是新的前提，只是从“内嵌页面连不上”扩大到“工作台也连不上”。
  - 前端开发（`npm run dev`，来源是 `http://localhost:5173`）需要在 `.env` 里加 `AGENT_ALLOWED_ORIGINS=http://localhost:5173`。
  - 系统页面的源必须出现在该系统清单的 `base_url` 或 `ui_bridge.embed_url` 里（R5.8）。
  - 它防的是“别的网页借用户的浏览器”，不是身份认证：能直接访问端口的本机进程或同网段机器不受它限制。默认只监听 127.0.0.1 的前提不变。
- 后续要做的事：接 SSO 时（P2-1）把确认通道绑定到登录身份，来源校验保留作为纵深防御。
