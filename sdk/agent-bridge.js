/*!
 * agent-bridge.js v0.2 —— 前端桥接 SDK（Baton）
 *
 * 作用：让已有 Web 系统把“界面上已有的操作”注册成 Agent 可调用的工具，系统后端无需改动。
 * 接口形态对齐 WebMCP（registerTool({name, description, inputSchema, annotations, execute})），
 * 浏览器提供原生 document.modelContext / navigator.modelContext 时会同时注册原生工具。
 *
 * 用法：
 *   <script src="http://<agent-host>/sdk/agent-bridge.js"></script>
 *   const bridge = AgentBridge.createAgentBridge({ system: 'fleet' });   // Agent 地址与工作台窗口默认取页面 URL 参数
 *   bridge.registerTool({ name, title, description, inputSchema, execute: async (args) => ({ ok: true, summary: '...' }) });
 *   bridge.provideViewState(() => ({ map_bbox: [...], selected_ids: [...] }));   // 可选：界面状态
 *   bridge.notifyViewChanged();                                                // 视图变化时调用（自动节流）
 *
 * 约定（接入规则第 5 节）：
 *   - 工具名为小写 snake_case，且必须在接入清单 ui_bridge.tools 中声明，否则会被拒绝；
 *   - execute 只调用页面已有方法，返回 { ok, summary, data? } 或字符串；不得修改业务数据；
 *   - 单次执行需在 20 秒内完成；界面状态不超过 2KB。
 */
(function (global) {
  'use strict';

  var RECONNECT_MIN_MS = 1000;
  var RECONNECT_MAX_MS = 10000;

  function readParams() {
    try {
      return new URLSearchParams(global.location.search);
    } catch (e) {
      return new URLSearchParams('');
    }
  }

  function toWs(url) {
    return url.replace(/^http(s?):\/\//i, function (_, s) { return 'ws' + s + '://'; });
  }

  function normalizeResult(value) {
    if (value === undefined || value === null) return { ok: true, summary: '已完成' };
    if (typeof value === 'string') return { ok: true, summary: value };
    if (typeof value !== 'object') return { ok: true, summary: String(value) };
    if (value.ok === false) {
      return {
        ok: false,
        summary: value.summary || '',
        error: value.error || { code: 'UI_ERROR', message: value.message || value.summary || '界面操作失败' },
      };
    }
    return { ok: true, summary: value.summary || '已完成', data: value.data };
  }

  function createAgentBridge(options) {
    options = options || {};
    var params = readParams();
    var system = options.system;
    if (!system) throw new Error('createAgentBridge: 缺少 system');
    var agentUrl = (options.agentUrl || params.get('agent') || '').replace(/\/+$/, '');
    // 内嵌在工作台时，工作台会追加 ?agent=<Agent 地址>&agent_client=<窗口 ID>（旧参数名 agent_session 仍兼容）
    var client = options.client || params.get('agent_client') || params.get('agent_session') || '';
    var page = options.page || (global.location ? global.location.pathname : '');
    var tools = {};
    var viewStateFn = null;
    var statusListeners = [];
    var ws = null;
    var status = 'idle';
    var retryMs = RECONNECT_MIN_MS;
    var stateTimer = null;
    var closed = false;
    var lastRegistration = { accepted: [], rejected: [] };

    function setStatus(s, detail) {
      status = s;
      statusListeners.forEach(function (fn) {
        try { fn(s, detail); } catch (e) { /* 忽略监听器错误 */ }
      });
    }

    function send(msg) {
      if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg));
    }

    function registerAll() {
      var list = Object.keys(tools).map(function (name) {
        var t = tools[name];
        return { name: name, title: t.title, description: t.description, inputSchema: t.inputSchema };
      });
      send({ type: 'register', tools: list });
    }

    function pushViewState() {
      if (!viewStateFn) return;
      try {
        send({ type: 'view_state', state: viewStateFn() || {} });
      } catch (e) {
        console.warn('[agent-bridge] provideViewState 出错', e);
      }
    }

    function handleCall(msg) {
      var tool = tools[msg.tool];
      var reply = function (result) { send({ type: 'result', call_id: msg.call_id, result: result }); };
      if (!tool) {
        reply({ ok: false, error: { code: 'UI_TOOL_NOT_FOUND', message: '页面没有注册工具 ' + msg.tool } });
        return;
      }
      Promise.resolve()
        .then(function () { return tool.execute(msg.args || {}, { bridge: api }); })
        .then(function (value) {
          reply(normalizeResult(value));
          pushViewState();
        })
        .catch(function (err) {
          reply({ ok: false, error: { code: (err && err.code) || 'UI_ERROR', message: String((err && err.message) || err) } });
        });
    }

    function connect() {
      if (closed || !agentUrl) {
        setStatus('disabled', agentUrl ? undefined : '未提供 Agent 地址（URL 参数 agent）');
        return;
      }
      var url = toWs(agentUrl) + '/ws/bridge?system=' + encodeURIComponent(system) +
        '&client=' + encodeURIComponent(client) + '&page=' + encodeURIComponent(page);
      setStatus('connecting');
      try {
        ws = new WebSocket(url);
      } catch (e) {
        scheduleReconnect();
        return;
      }
      ws.onopen = function () {
        retryMs = RECONNECT_MIN_MS;
        registerAll();
        pushViewState();
      };
      ws.onmessage = function (ev) {
        var msg;
        try { msg = JSON.parse(ev.data); } catch (e) { return; }
        if (msg.type === 'call') handleCall(msg);
        else if (msg.type === 'registered') {
          lastRegistration = { accepted: msg.accepted || [], rejected: msg.rejected || [] };
          if (lastRegistration.rejected.length) console.warn('[agent-bridge] 被拒绝的工具：', lastRegistration.rejected);
          setStatus('connected', lastRegistration);
        } else if (msg.type === 'error') {
          console.error('[agent-bridge]', msg.message);
          setStatus('rejected', msg.message);
        }
      };
      ws.onclose = function () {
        ws = null;
        if (status !== 'rejected') scheduleReconnect();
      };
      ws.onerror = function () { /* onclose 会处理重连 */ };
    }

    function scheduleReconnect() {
      if (closed) return;
      setStatus('disconnected');
      setTimeout(connect, retryMs);
      retryMs = Math.min(retryMs * 2, RECONNECT_MAX_MS);
    }

    function registerNative(name, tool) {
      // 浏览器原生 WebMCP（实验特性）：供浏览器内置 Agent 或扩展发现；失败不影响桥接
      var mc = (global.document && global.document.modelContext) || (global.navigator && global.navigator.modelContext);
      if (!mc || typeof mc.registerTool !== 'function') return;
      try {
        var ann = {};
        if (tool.risk === 'read') ann.readOnlyHint = true;
        mc.registerTool({
          name: system + '__' + name,
          description: tool.description,
          inputSchema: tool.inputSchema,
          annotations: ann,
          execute: function (args) {
            return Promise.resolve(tool.execute(args || {}, { bridge: api })).then(function (v) {
              return JSON.stringify(normalizeResult(v));
            });
          },
        });
      } catch (e) {
        console.debug('[agent-bridge] 原生 WebMCP 注册未成功：', e && e.message);
      }
    }

    var api = {
      system: system,
      agentUrl: agentUrl,
      client: client,

      /** 注册一个界面工具；risk 为本方案扩展字段（实际等级以接入清单为准）。 */
      registerTool: function (def) {
        if (!def || !/^[a-z][a-z0-9_]{1,40}$/.test(def.name || '')) throw new Error('工具名须为小写 snake_case：' + (def && def.name));
        if (typeof def.execute !== 'function') throw new Error('工具 ' + def.name + ' 缺少 execute');
        tools[def.name] = {
          title: def.title || '',
          description: def.description || '',
          inputSchema: def.inputSchema || { type: 'object', properties: {} },
          risk: def.risk || 'view',
          execute: def.execute,
        };
        registerNative(def.name, tools[def.name]);
        registerAll();
        return api;
      },

      unregisterTool: function (name) {
        delete tools[name];
        registerAll();
        return api;
      },

      /** 提供界面状态（视图范围、选中对象等，≤2KB），随每轮对话作为上下文给 Agent。 */
      provideViewState: function (fn) {
        viewStateFn = fn;
        pushViewState();
        return api;
      },

      /** 视图变化时调用，300ms 节流后推送界面状态。 */
      notifyViewChanged: function () {
        clearTimeout(stateTimer);
        stateTimer = setTimeout(pushViewState, 300);
      },

      /** 从结果仓库读取结果：format = geojson | rows | full */
      fetchResult: function (resultId, format) {
        if (!agentUrl) return Promise.reject(new Error('未连接 Agent'));
        var url = agentUrl + '/api/results/' + encodeURIComponent(resultId) + '?format=' + (format || 'geojson');
        return fetch(url).then(function (r) {
          if (!r.ok) return r.json().then(function (b) { throw new Error(b.detail || ('读取结果失败 ' + r.status)); });
          return r.json();
        });
      },

      onStatus: function (fn) {
        statusListeners.push(fn);
        try { fn(status, lastRegistration); } catch (e) { /* 忽略 */ }
        return api;
      },

      status: function () { return status; },

      close: function () {
        closed = true;
        if (ws) ws.close();
      },
    };

    setTimeout(connect, 0);
    return api;
  }

  global.AgentBridge = { createAgentBridge: createAgentBridge, version: '0.1.0' };
})(typeof window !== 'undefined' ? window : this);
