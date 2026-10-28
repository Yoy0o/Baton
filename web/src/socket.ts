/** 工作台与 Agent 服务之间的 WebSocket：自动重连；高频事件（逐字输出）合并成批，每 16ms 交给界面一次。 */
import type { AgentEvent } from './api';

export class AgentSocket {
  private ws: WebSocket | null = null;
  private queue: AgentEvent[] = [];
  private timer: number | null = null;
  private retryMs = 800;
  private closed = false;

  constructor(
    private readonly url: string,
    private readonly onEvents: (events: AgentEvent[]) => void,
    private readonly onStatus: (connected: boolean) => void,
  ) {}

  connect(): void {
    this.closed = false;
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.retryMs = 800;
      this.onStatus(true);
    };
    ws.onmessage = (ev) => {
      try {
        this.queue.push(JSON.parse(ev.data as string) as AgentEvent);
      } catch {
        return;
      }
      if (this.timer === null) this.timer = window.setTimeout(() => this.flush(), 16);
    };
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
      this.flush();
      this.onStatus(false);
      if (!this.closed) {
        window.setTimeout(() => this.connect(), this.retryMs);
        this.retryMs = Math.min(this.retryMs * 2, 8000);
      }
    };
  }

  private flush(): void {
    if (this.timer !== null) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    if (!this.queue.length) return;
    const batch = this.queue;
    this.queue = [];
    this.onEvents(batch);
  }

  send(message: Record<string, unknown>): boolean {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
      return true;
    }
    return false;
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
    this.ws = null;
  }
}

export function workbenchSocketUrl(clientId: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${location.host}/ws/workbench?client=${encodeURIComponent(clientId)}`;
}

/** 工作台窗口 ID：同一浏览器标签页刷新后不变，用来让内嵌的系统页面和对话对上。 */
export function getClientId(): string {
  const key = 'baton.client';
  try {
    const existing = sessionStorage.getItem(key);
    if (existing) return existing;
    const id = 'wb_' + Math.random().toString(36).slice(2, 12);
    sessionStorage.setItem(key, id);
    return id;
  } catch {
    return 'wb_' + Math.random().toString(36).slice(2, 12);
  }
}
