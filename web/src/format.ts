import type { Risk, ToolKind } from './api';

export const RISK_LABEL: Record<Risk, string> = {
  read: '只读',
  view: '只改界面',
  write: '写入',
  critical: '高风险写入',
};

export const KIND_LABEL: Record<ToolKind, string> = {
  api: 'API',
  ui: '界面',
  builtin: '内置',
};

export function formatDuration(ms?: number): string {
  if (ms === undefined || ms === null) return '';
  if (ms < 1000) return `${Math.max(ms, 0)} 毫秒`;
  const s = ms / 1000;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} 秒`;
  const m = Math.floor(s / 60);
  return `${m} 分 ${Math.round(s - m * 60)} 秒`;
}

export function formatClock(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

export function formatDateTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

/** 会话列表的时间分组 */
export function timeGroup(ts: number, now = Date.now()): string {
  const day = new Date(now);
  day.setHours(0, 0, 0, 0);
  const t = ts * 1000;
  if (t >= day.getTime()) return '今天';
  if (t >= day.getTime() - 86400000) return '昨天';
  if (t >= day.getTime() - 6 * 86400000) return '近 7 天';
  return '更早';
}

export const TIME_GROUP_ORDER = ['今天', '昨天', '近 7 天', '更早'];

export function formatCell(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (Array.isArray(v)) return v.length > 6 ? `${v.slice(0, 6).join('、')} 等 ${v.length} 项` : v.join('、');
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(Math.round(v * 1e6) / 1e6);
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

export function isResultId(text: string): boolean {
  return /^res_[0-9a-f]{8}$/.test(text);
}

export function toCsv(rows: Record<string, unknown>[]): string {
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  const cell = (v: unknown) => {
    const s = formatCell(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols.join(','), ...rows.map((r) => cols.map((c) => cell(r[c])).join(','))].join('\n');
}

export function download(filename: string, content: string, type: string): void {
  const blob = new Blob([type.includes('csv') ? '﻿' + content : content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
