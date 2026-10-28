import { XProvider } from '@ant-design/x';
import xZhCN from '@ant-design/x/es/locale/zh_CN';
import { App as AntApp, Button, Drawer, Result, theme as antdTheme } from 'antd';
import zhCN from 'antd/es/locale/zh_CN';
import { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from 'react';
import { api, type AgentEvent, type AppConfig, type Bridges, type ConversationMeta, type FeedbackValue } from './api';
import { ChatView } from './components/ChatView';
import { Sidebar, type ThemeMode } from './components/Sidebar';
import { sysTab, Workspace } from './components/Workspace';
import { WorkbenchContext, type OpenResultArgs, type WorkbenchContextValue } from './context';
import { useLocalStorage, useMediaQuery } from './hooks';
import { AgentSocket, getClientId, workbenchSocketUrl } from './socket';
import { DRAFT, resultsOf, viewsReducer, type Views } from './store';
import { buildTheme, cssVars } from './theme';

const LOCALE = { ...xZhCN, ...zhCN };
const NO_FEEDBACK: Record<string, FeedbackValue> = {};
const SIDEBAR_WIDTH = 264;
const CHAT_MIN_WIDTH = 420;

function parseHash(): string | null {
  const m = /^#\/c\/(c_[0-9a-f]{12})$/.exec(location.hash);
  return m ? m[1] : null;
}

export default function Root() {
  const [themeMode, setThemeMode] = useLocalStorage<ThemeMode>('baton.theme', 'system');
  const prefersDark = useMediaQuery('(prefers-color-scheme: dark)');
  const dark = themeMode === 'dark' || (themeMode === 'system' && prefersDark);
  const themeConfig = useMemo(() => buildTheme(dark), [dark]);
  return (
    <XProvider theme={themeConfig} locale={LOCALE} button={{ autoInsertSpace: false }}>
      <AntApp component={false}>
        <Workbench dark={dark} themeMode={themeMode} onThemeMode={setThemeMode} />
      </AntApp>
    </XProvider>
  );
}

type Conn = 'connecting' | 'open' | 'closed';

function Workbench({ dark, themeMode, onThemeMode }: { dark: boolean; themeMode: ThemeMode; onThemeMode: (m: ThemeMode) => void }) {
  const { token } = antdTheme.useToken();
  const { message, modal, notification } = AntApp.useApp();
  const clientId = useMemo(getClientId, []);

  // 主题变量挂在根节点上：弹层（下拉、气泡卡片、对话框）渲染在应用容器之外，也要能用
  useLayoutEffect(() => {
    const root = document.documentElement;
    for (const [k, v] of Object.entries(cssVars(dark, token))) root.style.setProperty(k, String(v));
    root.dataset.theme = dark ? 'dark' : 'light';
    root.style.colorScheme = dark ? 'dark' : 'light';
  }, [dark, token]);

  // ------------------------------------------------------------------ 状态
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationMeta[]>([]);
  const [activeId, setActiveIdState] = useState<string | null>(parseHash);
  const [views, dispatch] = useReducer(viewsReducer, {} as Views);
  const [conn, setConn] = useState<Conn>('connecting');
  const [allBridges, setAllBridges] = useState<Bridges>({});
  const [selected, setSelected] = useState<OpenResultArgs | null>(null);

  const wide = useMediaQuery('(min-width: 1100px)');
  const desktop = useMediaQuery('(min-width: 860px)');
  const [dockOpen, setDockOpen] = useLocalStorage('baton.workspace', window.innerWidth >= 1280);
  const [overlayOpen, setOverlayOpen] = useState(false);
  const [wsTab, setWsTab] = useState('results');
  const [wsWidth, setWsWidth] = useLocalStorage('baton.workspace.width', 0);
  const [sidebarOpen, setSidebarOpen] = useLocalStorage('baton.sidebar', true);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [resultsDot, setResultsDot] = useState(false);
  const [wsDot, setWsDot] = useState(false);
  const [auditTick, setAuditTick] = useState(0);
  const [resizing, setResizing] = useState(false);
  const wsOpen = wide ? dockOpen : overlayOpen;

  // 事件回调里读最新值用的引用
  const activeIdRef = useRef(activeId);
  const viewsRef = useRef(views);
  viewsRef.current = views;
  const conversationsRef = useRef(conversations);
  conversationsRef.current = conversations;
  const wideRef = useRef(wide);
  wideRef.current = wide;
  const wsTabRef = useRef(wsTab);
  wsTabRef.current = wsTab;
  const socketRef = useRef<AgentSocket | null>(null);
  const loadingRef = useRef(new Map<string, AgentEvent[]>());
  const callsRef = useRef(new Map<string, { system: string; kind: string }>());
  const uiRunRef = useRef<string | null>(null); // 这一轮操作了系统页面：结果展示不抢页签
  const heldRenderRef = useRef<string | null>(null);
  const draftPendingRef = useRef(false);
  const adoptingRef = useRef<string | null>(null);
  const wasClosedRef = useRef(false);

  const setActive = useCallback((id: string | null) => {
    activeIdRef.current = id;
    setActiveIdState(id);
  }, []);

  const navigate = useCallback(
    (id: string | null) => {
      const hash = id ? `#/c/${id}` : '#/';
      if (location.hash !== hash) location.hash = hash;
      setActive(id);
    },
    [setActive],
  );

  const patchMeta = (id: string, patch: Partial<ConversationMeta>) =>
    setConversations((list) => list.map((c) => (c.id === id ? { ...c, ...patch } : c)));

  // ------------------------------------------------------------------ 加载
  useEffect(() => {
    api
      .config()
      .then((c) => {
        setConfig(c);
        setAllBridges((b) => (Object.keys(b).length ? b : c.bridges));
        // 默认先看业务系统页面（如车队地图）
        const first = c.systems.find((s) => s.embed_url);
        if (first) setWsTab((t) => (t === 'results' ? sysTab(first.system) : t));
      })
      .catch((e: Error) => setConfigError(e.message));
    api
      .conversations()
      .then(setConversations)
      .catch(() => undefined);
  }, []);

  const loadConversation = useCallback(
    (id: string) => {
      if (loadingRef.current.has(id)) return;
      loadingRef.current.set(id, []);
      api
        .conversation(id)
        .then((d) => {
          const buffered = loadingRef.current.get(id) || [];
          loadingRef.current.delete(id);
          dispatch({ type: 'load', id, events: d.events, feedback: d.feedback || {} });
          if (buffered.length) dispatch({ type: 'events', events: buffered }); // 序号去重，重叠部分只应用一次
          const { events: _e, feedback: _f, ...meta } = d;
          void _e;
          void _f;
          setConversations((list) => (list.some((c) => c.id === id) ? list.map((c) => (c.id === id ? meta : c)) : [meta, ...list]));
        })
        .catch((e: Error) => {
          loadingRef.current.delete(id);
          if (activeIdRef.current === id) {
            message.warning(`没能打开这个对话：${e.message}`);
            navigate(null);
          }
        });
    },
    [message, navigate],
  );

  useEffect(() => {
    const onHash = () => {
      const id = parseHash();
      if (id !== activeIdRef.current) setActive(id);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, [setActive]);

  useEffect(() => {
    if (adoptingRef.current && adoptingRef.current === activeId) {
      adoptingRef.current = null; // 新建的会话接管了草稿，保留工作区里的结果选择
    } else {
      setSelected(null);
      setResultsDot(false);
    }
    if (!activeId) return;
    socketRef.current?.send({ type: 'open', conversation_id: activeId });
    if (!viewsRef.current[activeId]?.loaded) loadConversation(activeId);
  }, [activeId, loadConversation]);

  // ------------------------------------------------------------------ 实时事件
  const reveal = (tab: string) => {
    setWsTab(tab);
    if (tab === 'results') setResultsDot(false);
    if (wideRef.current) setDockOpen(true);
    else setWsDot(true); // 小屏不自动盖住对话，只在按钮上提示
  };

  const handleEvents = (batch: AgentEvent[]) => {
    let pending: AgentEvent[] = [];
    const flush = () => {
      if (pending.length) dispatch({ type: 'events', events: pending });
      pending = [];
    };
    for (const e of batch) {
      switch (e.type) {
        case 'hello':
          setAllBridges(e.bridges || {});
          if (wasClosedRef.current) {
            wasClosedRef.current = false;
            // 断线期间可能漏掉事件：刷新列表，重新拉取当前会话
            dispatch({ type: 'invalidate', keep: null });
            api
              .conversations()
              .then(setConversations)
              .catch(() => undefined);
            if (activeIdRef.current) loadConversation(activeIdRef.current);
          }
          if (activeIdRef.current) socketRef.current?.send({ type: 'open', conversation_id: activeIdRef.current });
          break;
        case 'bridge_status':
          setAllBridges(e.bridges || {});
          break;
        case 'pong':
          break;
        case 'conversation_created':
          flush();
          onCreated(e);
          break;
        case 'error':
          flush();
          message.error(String(e.message || '操作失败'));
          if (e.conversation_id) dispatch({ type: 'drop_placeholder', id: e.conversation_id });
          else if (draftPendingRef.current) {
            draftPendingRef.current = false;
            dispatch({ type: 'drop_placeholder', id: DRAFT });
          }
          break;
        default:
          if (e.run_id && e.conversation_id) {
            loadingRef.current.get(e.conversation_id)?.push(e);
            pending.push(e);
            sideEffects(e);
          }
      }
    }
    flush();
  };

  const onCreated = (e: AgentEvent) => {
    const meta: ConversationMeta = {
      id: String(e.id || e.conversation_id),
      title: String(e.title || '新对话'),
      created_at: Number(e.created_at),
      updated_at: Number(e.updated_at),
      running: true,
      pending_confirm: false,
    };
    setConversations((list) => [meta, ...list.filter((c) => c.id !== meta.id)]);
    if (!draftPendingRef.current) return;
    draftPendingRef.current = false;
    dispatch({ type: 'adopt_draft', id: meta.id });
    if (activeIdRef.current === null) {
      adoptingRef.current = meta.id;
      history.replaceState(null, '', `#/c/${meta.id}`);
      setActive(meta.id);
    }
  };

  const sideEffects = (e: AgentEvent) => {
    const cid = String(e.conversation_id);
    const isActive = cid === activeIdRef.current;
    const now = Date.now() / 1000;
    switch (e.type) {
      case 'run_started':
        patchMeta(cid, { running: true, updated_at: now });
        break;
      case 'confirm_request':
        patchMeta(cid, { pending_confirm: true });
        if (!isActive) {
          const title = conversationsRef.current.find((c) => c.id === cid)?.title || '另一个对话';
          const key = `confirm-${cid}`;
          notification.warning({
            key,
            title: '有写操作等你确认',
            description: `“${title}”需要你确认后才会提交到业务系统。`,
            duration: 0,
            actions: (
              <Button
                type="primary"
                size="small"
                onClick={() => {
                  notification.destroy(key);
                  navigate(cid);
                }}
              >
                去确认
              </Button>
            ),
          });
        }
        break;
      case 'confirm_resolved':
        patchMeta(cid, { pending_confirm: false });
        notification.destroy(`confirm-${cid}`);
        break;
      case 'run_finished':
        patchMeta(cid, { running: false, pending_confirm: false, updated_at: now });
        notification.destroy(`confirm-${cid}`);
        setAuditTick((t) => t + 1);
        break;
      case 'tool_call_started':
        callsRef.current.set(String(e.call_id), { system: String(e.system || ''), kind: String(e.kind || '') });
        if (e.kind === 'ui') uiRunRef.current = String(e.run_id);
        break;
      case 'tool_call_finished': {
        setAuditTick((t) => t + 1);
        const info = callsRef.current.get(String(e.call_id));
        if (!isActive) break;
        if (info?.kind === 'ui') {
          if (e.ok) reveal(sysTab(info.system));
          else if (heldRenderRef.current === e.run_id) reveal('results'); // 页面没显示成，改看工作台里的结果
        } else if (e.ok && e.result_id && wsTabRef.current !== 'results') {
          setResultsDot(true);
        }
        break;
      }
      case 'render':
        if (!isActive) break;
        setSelected({ resultId: String(e.result_id), colorBy: e.color_by, title: e.title });
        if (uiRunRef.current === e.run_id) {
          heldRenderRef.current = String(e.run_id);
          setResultsDot(true);
        } else reveal('results');
        break;
    }
  };

  const handlerRef = useRef(handleEvents);
  handlerRef.current = handleEvents;

  useEffect(() => {
    const socket: AgentSocket = new AgentSocket(
      workbenchSocketUrl(clientId),
      (events) => handlerRef.current(events),
      (ok) => {
        if (socketRef.current !== socket) return;
        if (!ok) wasClosedRef.current = true;
        setConn(ok ? 'open' : 'closed');
      },
    );
    socketRef.current = socket;
    socket.connect();
    const ping = window.setInterval(() => socket.send({ type: 'ping' }), 25000);
    return () => {
      window.clearInterval(ping);
      socketRef.current = null;
      socket.close();
    };
  }, [clientId]);

  // ------------------------------------------------------------------ 操作
  const send = useCallback(
    (text: string) => {
      const id = activeIdRef.current;
      const key = id ?? DRAFT;
      dispatch({ type: 'optimistic', id: key, text });
      const ok = socketRef.current?.send(id ? { type: 'user_message', conversation_id: id, text } : { type: 'user_message', text });
      if (!ok) {
        dispatch({ type: 'drop_placeholder', id: key });
        message.error('没有连接到服务，消息没有发出，请稍后再试');
        return;
      }
      if (!id) draftPendingRef.current = true;
    },
    [message],
  );

  const command = useCallback(
    (msg: Record<string, unknown>) => {
      const id = activeIdRef.current;
      if (!id) return false;
      const ok = socketRef.current?.send({ ...msg, conversation_id: id });
      if (!ok) message.error('没有连接到服务，请稍后再试');
      return Boolean(ok);
    },
    [message],
  );

  const stop = useCallback(() => command({ type: 'stop' }), [command]);
  const regenerate = useCallback(() => void command({ type: 'regenerate' }), [command]);
  const decide = useCallback((confirmId: string, approved: boolean) => void command({ type: 'confirm_decision', confirm_id: confirmId, approved }), [command]);

  const setFeedback = useCallback(
    (runId: string, value: FeedbackValue | null) => {
      const id = activeIdRef.current;
      if (!id) return;
      dispatch({ type: 'feedback', id, runId, value });
      api.feedback(id, runId, value ?? 'default').catch(() => message.error('反馈没有保存，请稍后再试'));
    },
    [message],
  );

  const openResult = useCallback(
    (args: OpenResultArgs) => {
      setSelected(args);
      setWsTab('results');
      setResultsDot(false);
      if (wideRef.current) setDockOpen(true);
      else setOverlayOpen(true);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const newChat = useCallback(() => {
    setDrawerOpen(false);
    if (!draftPendingRef.current) dispatch({ type: 'remove', id: DRAFT });
    navigate(null);
    window.setTimeout(() => document.querySelector<HTMLTextAreaElement>('.bt-sender textarea')?.focus(), 50);
  }, [navigate]);

  const selectConversation = useCallback(
    (id: string) => {
      setDrawerOpen(false);
      navigate(id);
    },
    [navigate],
  );

  const rename = useCallback(
    async (id: string, title: string) => {
      try {
        const meta = await api.rename(id, title);
        patchMeta(id, { title: meta.title });
        message.success('已重命名');
      } catch (e) {
        message.error(`重命名失败：${(e as Error).message}`);
        throw e;
      }
    },
    [message],
  );

  const remove = useCallback(
    (id: string) => {
      const conv = conversationsRef.current.find((c) => c.id === id);
      modal.confirm({
        title: '删除这个对话？',
        content: `“${conv?.title || ''}”的对话记录会被删除${conv?.running ? '，正在执行的任务会停止' : ''}。已经提交到业务系统的修改不受影响。`,
        okText: '删除',
        okButtonProps: { danger: true },
        cancelText: '取消',
        onOk: async () => {
          try {
            await api.remove(id);
          } catch (e) {
            message.error(`删除失败：${(e as Error).message}`);
            return;
          }
          setConversations((list) => list.filter((c) => c.id !== id));
          dispatch({ type: 'remove', id });
          if (activeIdRef.current === id) navigate(null);
          message.success('已删除');
        },
      });
    },
    [modal, message, navigate],
  );

  // ------------------------------------------------------------------ 派生
  const bridges = useMemo<Bridges>(
    () => Object.fromEntries(Object.entries(allBridges).map(([s, list]) => [s, list.filter((c) => c.client_id === clientId)])),
    [allBridges, clientId],
  );
  const view = views[activeId ?? DRAFT];
  const running = Boolean(view?.runs.some((r) => r.status === 'running'));
  const feedback = view?.feedback ?? NO_FEEDBACK;
  const results = useMemo(() => resultsOf(view), [view]);
  const activeMeta = conversations.find((c) => c.id === activeId);
  const title = activeId ? activeMeta?.title || '对话' : '新对话';
  const appName = config?.app.name || 'Baton';

  useEffect(() => {
    const waiting = conversations.filter((c) => c.pending_confirm).length;
    document.title = `${waiting ? `(${waiting}) ` : ''}${activeMeta ? `${activeMeta.title} - ` : ''}${appName}`;
  }, [conversations, activeMeta, appName]);

  const ctx = useMemo<WorkbenchContextValue>(
    () => ({
      config,
      clientId,
      conversationId: activeId,
      systemsMeta: config?.systems_meta ?? {},
      bridges,
      running,
      feedback,
      openResult,
      send,
      regenerate,
      decide,
      setFeedback,
    }),
    [config, clientId, activeId, bridges, running, feedback, openResult, send, regenerate, decide, setFeedback],
  );

  // ------------------------------------------------------------------ 工作区宽度
  const clampWidth = (w: number) =>
    Math.round(Math.max(340, Math.min(w, window.innerWidth - (desktop && sidebarOpen ? SIDEBAR_WIDTH : 0) - CHAT_MIN_WIDTH)));

  const startResize = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const aside = e.currentTarget.parentElement as HTMLElement;
    const startX = e.clientX;
    const startW = aside.getBoundingClientRect().width;
    setResizing(true);
    const move = (ev: PointerEvent) => {
      aside.style.width = `${clampWidth(startW + startX - ev.clientX)}px`;
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      setResizing(false);
      setWsWidth(Math.round(aside.getBoundingClientRect().width));
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  const resizeByKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const aside = e.currentTarget.parentElement as HTMLElement;
    const w = clampWidth(aside.getBoundingClientRect().width + (e.key === 'ArrowLeft' ? 40 : -40));
    aside.style.width = `${w}px`;
    setWsWidth(w);
  };

  const toggleWorkspace = () => {
    setWsDot(false);
    if (wide) setDockOpen(!dockOpen);
    else setOverlayOpen(!overlayOpen);
  };

  useEffect(() => {
    if (!overlayOpen || wide) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOverlayOpen(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [overlayOpen, wide]);

  if (configError) {
    return (
      <div className="bt-fatal">
        <Result
          status="error"
          title="连接不上 Agent 服务"
          subTitle={`工作台需要 Agent 服务提供配置（/api/config）：${configError}。确认服务已启动后重试。`}
          extra={
            <Button type="primary" onClick={() => location.reload()}>
              重试
            </Button>
          }
        />
      </div>
    );
  }

  const sidebar = (
    <Sidebar
      appName={appName}
      tagline={config?.app.tagline || ''}
      version={config?.app.version || ''}
      conversations={conversations}
      activeId={activeId}
      connected={conn !== 'closed'}
      themeMode={themeMode}
      onThemeMode={onThemeMode}
      onNew={newChat}
      onSelect={selectConversation}
      onRename={rename}
      onDelete={remove}
      onCollapse={desktop ? () => setSidebarOpen(false) : undefined}
    />
  );

  return (
    <WorkbenchContext.Provider value={ctx}>
      <div className={`bt-app ${resizing ? 'is-resizing' : ''}`}>
        {desktop ? (
          sidebarOpen && sidebar
        ) : (
          <Drawer
            placement="left"
            open={drawerOpen}
            onClose={() => setDrawerOpen(false)}
            size={300}
            closable={false}
            rootClassName="bt-drawer"
            styles={{ body: { padding: 0 } }}
          >
            {sidebar}
          </Drawer>
        )}

        <main className="bt-main">
          <ChatView
            title={title}
            view={view}
            loading={Boolean(activeId) && !view?.loaded}
            connected={conn === 'open'}
            disconnected={conn === 'closed'}
            running={running}
            workspaceOpen={wsOpen}
            workspaceDot={wsDot}
            showMenu={!desktop || !sidebarOpen}
            onMenu={() => (desktop ? setSidebarOpen(true) : setDrawerOpen(true))}
            onNew={newChat}
            onToggleWorkspace={toggleWorkspace}
            onSend={send}
            onStop={stop}
          />
        </main>

        <aside
          className={`bt-workspace ${wide ? 'is-docked' : 'is-overlay'} ${wsOpen ? 'is-open' : ''}`}
          style={wide && wsWidth ? { width: clampWidth(wsWidth) } : undefined}
          aria-label="工作区"
        >
          {wide && (
            <div
              className="bt-resizer"
              role="separator"
              aria-orientation="vertical"
              aria-label="拖动调整工作区宽度"
              tabIndex={0}
              onPointerDown={startResize}
              onKeyDown={resizeByKey}
            />
          )}
          <Workspace
            tab={wsTab}
            onTab={(key) => {
              setWsTab(key);
              if (key === 'results') setResultsDot(false);
            }}
            onClose={() => (wide ? setDockOpen(false) : setOverlayOpen(false))}
            results={results}
            selected={selected}
            onSelectResult={setSelected}
            resultsDot={resultsDot}
            refreshKey={`${activeId}:${auditTick}:${Object.values(bridges).flat().length}`}
          />
        </aside>
        {!wide && overlayOpen && <div className="bt-ws-mask" onClick={() => setOverlayOpen(false)} aria-hidden />}
      </div>
    </WorkbenchContext.Provider>
  );
}
