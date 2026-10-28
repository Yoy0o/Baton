/** 与 Agent 服务交互的类型与 REST 接口。事件协议见 docs/ARCHITECTURE.md 第 3.5 节。 */

export type Risk = 'read' | 'view' | 'write' | 'critical';
export type ToolKind = 'api' | 'ui' | 'builtin';
export type Row = Record<string, unknown>;

export interface SystemInfo {
  system: string;
  title: string;
  description: string;
  color: string;
  ui_title: string;
  embed_url: string | null;
  api_tools: number;
  ui_tools: { tool: string; title: string }[];
}

export interface SystemMeta {
  title: string;
  ui_title: string;
  color: string;
  builtin: boolean;
}

export interface BridgeConn {
  client_id: string;
  page: string;
  tools: string[];
  titles: Record<string, string>;
  connected_at: number;
}

export type Bridges = Record<string, BridgeConn[]>;

export interface AppConfig {
  app: { name: string; tagline: string; version: string };
  version: string;
  mode: 'demo' | 'llm';
  planner: string;
  agent_url: string;
  systems: SystemInfo[];
  systems_meta: Record<string, SystemMeta>;
  load_errors: Record<string, string[]>;
  bridges: Bridges;
}

export interface ConversationMeta {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  running: boolean;
  pending_confirm: boolean;
}

export type FeedbackValue = 'like' | 'dislike';

export interface ConversationDetail extends ConversationMeta {
  events: AgentEvent[];
  feedback: Record<string, FeedbackValue>;
}

/** 服务端推送的事件；字段随 type 变化。 */
export interface AgentEvent {
  type: string;
  conversation_id?: string | null;
  run_id?: string;
  /** 需要重放的事件才有序号（逐字输出等实时事件没有） */
  seq?: number;
  [key: string]: any;
}

export interface ToolInfo {
  name: string;
  system: string;
  system_title: string;
  color: string;
  kind: ToolKind;
  risk: Risk;
  risk_label: string;
  title: string;
  description: string;
  input_schema: { properties?: Record<string, { type?: string; description?: string; default?: unknown; enum?: unknown[] }>; required?: string[] };
  pairs_with: string | null;
}

export interface AuditEntry {
  ts: number;
  user: string;
  conversation_id: string;
  client_id: string;
  run_id: string;
  request_id: string;
  initiator: 'agent' | 'user' | 'mcp';
  tool: string;
  title: string | null;
  system: string;
  kind: ToolKind | null;
  risk: Risk | null;
  args: string;
  ok: boolean;
  error_code: string | null;
  summary: string;
  result_id: string | null;
  confirmed: boolean | null;
  duration_ms: number;
}

export type ResultType = 'table' | 'feature_collection' | 'group_plan' | 'object';

export interface GroupStat {
  group_no: number;
  size: number;
  member_ids: string[];
  centroid: [number, number];
  avg_dist_km: number;
  max_dist_km: number;
}

export interface Feature {
  type: 'Feature';
  geometry: { type: string; coordinates: number[] } | null;
  properties: Row | null;
}

export interface ResultFull {
  result_id: string;
  result_type: ResultType;
  summary: string;
  total: number | null;
  fields: string[];
  source: { tool?: string; args?: Row };
  created_at: number;
  data: { features?: Feature[]; groups?: GroupStat[]; rows?: Row[] } & Row;
  rows: Row[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  });
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try {
      const body = await resp.json();
      detail = body.detail || body.message || detail;
    } catch {
      /* 非 JSON 响应 */
    }
    throw new Error(detail);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  config: () => request<AppConfig>('/api/config'),
  conversations: () => request<{ items: ConversationMeta[] }>('/api/conversations').then((r) => r.items),
  conversation: (id: string) => request<ConversationDetail>(`/api/conversations/${encodeURIComponent(id)}`),
  rename: (id: string, title: string) =>
    request<ConversationMeta>(`/api/conversations/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ title }) }),
  remove: (id: string) => request<{ ok: boolean }>(`/api/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  feedback: (conversationId: string, runId: string, value: FeedbackValue | 'default') =>
    request<{ ok: boolean }>('/api/feedback', {
      method: 'POST',
      body: JSON.stringify({ conversation_id: conversationId, run_id: runId, value }),
    }),
  tools: (clientId: string) => request<{ tools: ToolInfo[]; load_errors: Record<string, string[]> }>(`/api/tools?client=${encodeURIComponent(clientId)}`),
  audit: (conversationId?: string) =>
    request<{ entries: AuditEntry[] }>(
      `/api/audit?limit=200${conversationId ? `&conversation_id=${encodeURIComponent(conversationId)}` : ''}`,
    ).then((r) => r.entries),
  result: (id: string) => request<ResultFull>(`/api/results/${encodeURIComponent(id)}`),
  uiAction: (clientId: string, conversationId: string | null, tool: string, args: Row) =>
    request<{ ok: boolean; summary?: string; message?: string; fix_hint?: string }>('/api/ui-actions', {
      method: 'POST',
      body: JSON.stringify({ client_id: clientId, conversation_id: conversationId, tool, args }),
    }),
};

export function resultGeoJsonUrl(id: string): string {
  return `/api/results/${encodeURIComponent(id)}?format=geojson`;
}
