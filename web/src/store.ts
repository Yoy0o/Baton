/**
 * 会话视图：由服务端事件“重放”得到。实时事件和打开历史会话时加载的事件走同一个 reducer，
 * 所以刷新页面、切换会话后看到的执行过程、确认卡片、答复与实时看到的一致。
 */
import type { AgentEvent, FeedbackValue, Risk, Row, ToolKind } from './api';

export type StepStatus = 'running' | 'waiting' | 'success' | 'error' | 'cancelled';
export type RunStatus = 'running' | 'completed' | 'stopped' | 'error' | 'interrupted';
export type ConfirmState = 'pending' | 'approved' | 'rejected' | 'timeout' | 'stopped' | 'interrupted';

export interface ToolStep {
  kind: 'tool';
  callId: string;
  tool: string;
  title: string;
  system: string;
  systemTitle: string;
  color: string;
  toolKind: ToolKind | null;
  risk: Risk | null;
  args: Row;
  status: StepStatus;
  durationMs?: number;
  summary?: string;
  errorCode?: string;
  message?: string;
  fixHint?: string;
  resultId?: string;
  resultType?: string;
  total?: number;
  preview?: Row[];
  changes?: Row[];
  columns?: string[];
}

export interface NoteStep {
  kind: 'note';
  id: string;
  text: string;
}

export type Step = ToolStep | NoteStep;

export interface ConfirmInfo {
  confirmId: string;
  tool: string;
  title: string;
  systemTitle: string;
  risk: Risk;
  summary: string;
  changes: Row[];
  columns: string[];
  expiresAt: number;
  state: ConfirmState;
}

export interface RenderInfo {
  resultId: string;
  resultType?: string;
  colorBy?: string | null;
  title?: string;
}

export interface Run {
  runId: string;
  text: string;
  planner: string;
  startedAt: number;
  placeholder?: boolean;
  steps: Step[];
  liveText: string;
  answer?: string;
  partial?: boolean;
  reasoning: string;
  liveReasoning: string;
  status: RunStatus;
  error?: string;
  confirms: ConfirmInfo[];
  renders: RenderInfo[];
  durationMs?: number;
  suggestions: string[];
}

export interface ConvView {
  runs: Run[];
  feedback: Record<string, FeedbackValue>;
  loaded: boolean;
  /** 已应用的最大事件序号；打开会话时拉取的快照和实时事件可能重叠，靠它去重。 */
  seq: number;
}

export const DRAFT = '__draft__';

export const emptyView = (loaded = false): ConvView => ({ runs: [], feedback: {}, loaded, seq: 0 });

function joinText(a: string, b?: string): string {
  if (!b) return a;
  return a ? `${a}\n\n${b}` : b;
}

function newRun(e: AgentEvent): Run {
  return {
    runId: String(e.run_id),
    text: String(e.text ?? ''),
    planner: String(e.planner ?? ''),
    startedAt: Number(e.started_at ?? Date.now() / 1000),
    steps: [],
    liveText: '',
    reasoning: '',
    liveReasoning: '',
    status: 'running',
    confirms: [],
    renders: [],
    suggestions: [],
  };
}

export function placeholderRun(text: string): Run {
  return { ...newRun({ type: 'run_started', run_id: `pending_${Date.now()}`, text }), placeholder: true };
}

function toolStep(e: AgentEvent): ToolStep {
  return {
    kind: 'tool',
    callId: String(e.call_id),
    tool: String(e.tool),
    title: String(e.title || e.tool),
    system: String(e.system || ''),
    systemTitle: String(e.system_title || e.system || ''),
    color: String(e.color || '#64748B'),
    toolKind: (e.kind ?? null) as ToolKind | null,
    risk: (e.risk ?? null) as Risk | null,
    args: (e.args || {}) as Row,
    status: 'running',
  };
}

function finishStep(s: ToolStep, e: AgentEvent): ToolStep {
  const cancelled = e.error_code === 'CANCELLED';
  return {
    ...s,
    status: e.ok ? 'success' : cancelled ? 'cancelled' : 'error',
    durationMs: e.duration_ms,
    summary: e.summary,
    errorCode: e.error_code,
    message: e.message,
    fixHint: e.fix_hint,
    resultId: e.result_id,
    resultType: e.result_type,
    total: e.total,
    preview: e.preview,
    changes: e.changes,
    columns: e.columns,
  };
}

function reduceRun(r: Run, e: AgentEvent): Run {
  switch (e.type) {
    case 'text_delta':
      return { ...r, liveText: r.liveText + String(e.delta ?? '') };
    case 'reasoning_delta':
      return { ...r, liveReasoning: r.liveReasoning + String(e.delta ?? '') };
    case 'thought': {
      const steps: Step[] = e.text ? [...r.steps, { kind: 'note', id: `note_${r.steps.length}`, text: String(e.text) }] : r.steps;
      return { ...r, steps, liveText: '', reasoning: joinText(r.reasoning, e.reasoning), liveReasoning: '' };
    }
    case 'tool_call_started':
      return { ...r, steps: [...r.steps, toolStep(e)] };
    case 'tool_call_finished':
      return {
        ...r,
        steps: r.steps.map((s) => (s.kind === 'tool' && s.callId === e.call_id ? finishStep(s, e) : s)),
      };
    case 'confirm_request': {
      let marked = false;
      const steps = [...r.steps].reverse().map((s) => {
        if (!marked && s.kind === 'tool' && s.tool === e.tool && s.status === 'running') {
          marked = true;
          return { ...s, status: 'waiting' as StepStatus };
        }
        return s;
      }).reverse();
      const confirm: ConfirmInfo = {
        confirmId: String(e.confirm_id),
        tool: String(e.tool),
        title: String(e.title || e.tool),
        systemTitle: String(e.system_title || ''),
        risk: (e.risk || 'write') as Risk,
        summary: String(e.summary || ''),
        changes: (e.changes || []) as Row[],
        columns: (e.columns || []) as string[],
        expiresAt: Number(e.expires_at || 0),
        state: 'pending',
      };
      return { ...r, steps, confirms: [...r.confirms, confirm] };
    }
    case 'confirm_resolved': {
      const state: ConfirmState = e.approved
        ? 'approved'
        : e.reason === 'timeout'
          ? 'timeout'
          : e.reason === 'stopped'
            ? 'stopped'
            : e.reason === 'interrupted'
              ? 'interrupted'
              : 'rejected';
      return {
        ...r,
        confirms: r.confirms.map((c) => (c.confirmId === e.confirm_id ? { ...c, state } : c)),
        steps: r.steps.map((s) => (s.kind === 'tool' && s.status === 'waiting' ? { ...s, status: 'running' as StepStatus } : s)),
      };
    }
    case 'render':
      return {
        ...r,
        renders: [...r.renders, { resultId: String(e.result_id), resultType: e.result_type, colorBy: e.color_by, title: e.title }],
      };
    case 'message':
      return {
        ...r,
        answer: String(e.text ?? ''),
        partial: Boolean(e.partial),
        liveText: '',
        reasoning: joinText(r.reasoning, e.reasoning),
        liveReasoning: '',
      };
    case 'run_error':
      return { ...r, error: String(e.message || '运行出错') };
    case 'run_finished': {
      const status = (e.status || 'completed') as RunStatus;
      const endState: ConfirmState = status === 'interrupted' ? 'interrupted' : 'stopped';
      return {
        ...r,
        status,
        durationMs: e.duration_ms,
        suggestions: (e.suggestions || []) as string[],
        liveText: r.answer ? '' : r.liveText,
        steps: r.steps.map((s) =>
          s.kind === 'tool' && (s.status === 'running' || s.status === 'waiting') ? { ...s, status: 'cancelled' as StepStatus } : s,
        ),
        confirms: r.confirms.map((c) => (c.state === 'pending' ? { ...c, state: endState } : c)),
      };
    }
    default:
      return r;
  }
}

export function reduceView(view: ConvView, e: AgentEvent): ConvView {
  if (typeof e.seq === 'number') {
    if (e.seq <= view.seq) return view; // 已在快照里
    view = { ...view, seq: e.seq };
  }
  if (e.type === 'run_started') {
    const run = newRun(e);
    const idx = view.runs.findIndex((r) => r.placeholder && r.text === run.text);
    const runs = idx >= 0 ? view.runs.map((r, i) => (i === idx ? run : r)) : [...view.runs, run];
    return { ...view, runs };
  }
  if (e.type === 'run_removed') {
    return { ...view, runs: view.runs.filter((r) => r.runId !== e.run_id) };
  }
  if (!e.run_id) return view;
  let touched = false;
  const runs = view.runs.map((r) => {
    if (r.runId !== e.run_id) return r;
    touched = true;
    return reduceRun(r, e);
  });
  return touched ? { ...view, runs } : view;
}

export function replay(events: AgentEvent[], feedback: Record<string, FeedbackValue>): ConvView {
  return events.reduce(reduceView, { runs: [], feedback, loaded: true, seq: 0 });
}

// ------------------------------------------------------------------ 所有会话视图

export type Views = Record<string, ConvView>;

export type ViewsAction =
  | { type: 'load'; id: string; events: AgentEvent[]; feedback: Record<string, FeedbackValue> }
  | { type: 'events'; events: AgentEvent[] }
  | { type: 'optimistic'; id: string; text: string }
  | { type: 'drop_placeholder'; id: string }
  | { type: 'adopt_draft'; id: string }
  | { type: 'feedback'; id: string; runId: string; value: FeedbackValue | null }
  | { type: 'remove'; id: string }
  | { type: 'invalidate'; keep: string | null };

export function viewsReducer(state: Views, action: ViewsAction): Views {
  switch (action.type) {
    case 'load':
      return { ...state, [action.id]: replay(action.events, action.feedback) };
    case 'events': {
      let next = state;
      for (const e of action.events) {
        const id = e.conversation_id;
        if (!id || !next[id]?.loaded) continue; // 未加载的会话打开时再整体拉取
        next = { ...next, [id]: reduceView(next[id], e) };
      }
      return next;
    }
    case 'optimistic': {
      const view = state[action.id] || emptyView(true);
      return { ...state, [action.id]: { ...view, runs: [...view.runs, placeholderRun(action.text)] } };
    }
    case 'drop_placeholder': {
      const view = state[action.id];
      if (!view) return state;
      return { ...state, [action.id]: { ...view, runs: view.runs.filter((r) => !r.placeholder) } };
    }
    case 'adopt_draft': {
      const draft = state[DRAFT] || emptyView(true);
      const { [DRAFT]: _omit, ...rest } = state;
      void _omit;
      return { ...rest, [action.id]: { ...draft, loaded: true } };
    }
    case 'feedback': {
      const view = state[action.id];
      if (!view) return state;
      const feedback = { ...view.feedback };
      if (action.value) feedback[action.runId] = action.value;
      else delete feedback[action.runId];
      return { ...state, [action.id]: { ...view, feedback } };
    }
    case 'remove': {
      const { [action.id]: _gone, ...rest } = state;
      void _gone;
      return rest;
    }
    case 'invalidate': {
      // 断线期间可能漏掉事件：除当前会话（马上重新拉取）外，其他会话下次打开时重新拉取
      const next: Views = {};
      for (const [id, view] of Object.entries(state)) next[id] = id === action.keep || id === DRAFT ? view : { ...view, loaded: false };
      return next;
    }
    default:
      return state;
  }
}

/** 本会话中产生过的结果（按出现顺序，去重）。 */
export function resultsOf(view: ConvView | undefined): { resultId: string; resultType?: string; summary?: string; total?: number; tool: string; color: string; systemTitle: string }[] {
  const out = new Map<string, { resultId: string; resultType?: string; summary?: string; total?: number; tool: string; color: string; systemTitle: string }>();
  for (const run of view?.runs || []) {
    for (const s of run.steps) {
      if (s.kind === 'tool' && s.resultId && !out.has(s.resultId)) {
        out.set(s.resultId, {
          resultId: s.resultId,
          resultType: s.resultType,
          summary: s.summary,
          total: s.total,
          tool: s.tool,
          color: s.color,
          systemTitle: s.systemTitle,
        });
      }
    }
  }
  return [...out.values()];
}
