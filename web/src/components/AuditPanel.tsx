import { ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Empty, Segmented, Skeleton, Tooltip } from 'antd';
import { useEffect, useState, type CSSProperties } from 'react';
import { api, type AuditEntry } from '../api';
import { useWorkbench } from '../context';
import { formatDateTime, formatDuration } from '../format';
import { ResultChip } from './ResultChip';

const INITIATOR: Record<AuditEntry['initiator'], string> = { agent: 'Agent', user: '你在工作台操作', mcp: 'MCP 调试' };

/** 审计：每次工具调用一条，谁发起、调了什么、是否确认、结果如何。 */
export function AuditPanel({ active, refreshKey }: { active: boolean; refreshKey: string }) {
  const { conversationId, systemsMeta } = useWorkbench();
  const [scope, setScope] = useState<'conversation' | 'all'>('conversation');
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [attempt, setAttempt] = useState(0);
  const filter = scope === 'conversation' ? conversationId : null;

  useEffect(() => {
    if (!active) return;
    if (scope === 'conversation' && !conversationId) {
      setEntries([]);
      return;
    }
    let alive = true;
    setFailure(null);
    api
      .audit(filter ?? undefined)
      .then((list) => alive && setEntries(list))
      .catch((e: Error) => alive && setFailure(e.message));
    return () => {
      alive = false;
    };
  }, [active, scope, filter, conversationId, refreshKey, attempt]);

  return (
    <div className="bt-audit">
      <div className="bt-panel-toolbar">
        <Segmented
          value={scope}
          onChange={(v) => {
            setScope(v as 'conversation' | 'all');
            setOpen(null);
          }}
          options={[
            { value: 'conversation', label: '本对话' },
            { value: 'all', label: '全部' },
          ]}
        />
        <span className="bt-panel-count">{entries ? `${entries.length} 条` : ''}</span>
        <Tooltip title="刷新">
          <Button icon={<ReloadOutlined />} onClick={() => setAttempt(attempt + 1)} aria-label="刷新审计记录" />
        </Tooltip>
      </div>
      <p className="bt-panel-note">每次工具调用都会记录，包括你在工作台直接发起的操作。服务重启后只显示新的记录，完整日志在 data/audit.jsonl。</p>
      {failure ? (
        <Alert type="error" showIcon title="审计记录加载失败" description={failure} />
      ) : !entries ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : entries.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={scope === 'conversation' ? '这次对话还没有工具调用' : '还没有工具调用'} />
      ) : (
        <ol className="bt-audit-list">
          {entries.map((e, i) => {
            const meta = systemsMeta[e.system];
            const write = e.risk === 'write' || e.risk === 'critical';
            const expanded = open === i;
            return (
              <li key={`${e.ts}-${i}`} className={`bt-audit-row ${e.ok ? 'is-ok' : 'is-error'}`} style={{ '--c': meta?.color || '#64748B' } as CSSProperties}>
                <button type="button" className="bt-audit-main" aria-expanded={expanded} onClick={() => setOpen(expanded ? null : i)}>
                  <span className="bt-audit-title">
                    <i className="bt-audit-dot" aria-hidden />
                    <span className="bt-audit-name">{e.title || e.tool}</span>
                    <span className="bt-sys" style={{ '--c': meta?.color || '#64748B' } as CSSProperties}>
                      {meta?.title || e.system}
                    </span>
                    {write && <span className="bt-audit-tag is-write">{e.confirmed === true ? '已确认写入' : e.confirmed === false ? '未确认' : '写入'}</span>}
                    {!e.ok && <span className="bt-audit-tag is-error">{e.error_code || '失败'}</span>}
                  </span>
                  <span className="bt-audit-summary">{e.summary}</span>
                  <span className="bt-audit-meta">
                    <time>{formatDateTime(e.ts)}</time>
                    <span>{formatDuration(e.duration_ms)}</span>
                    <span>{INITIATOR[e.initiator] || e.initiator}</span>
                  </span>
                </button>
                {expanded && (
                  <div className="bt-audit-detail">
                    <code>{e.tool}</code>
                    {e.result_id && <ResultChip id={e.result_id} />}
                    <pre className="bt-pre">{prettyArgs(e.args)}</pre>
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

function prettyArgs(args: string): string {
  try {
    return JSON.stringify(JSON.parse(args), null, 2);
  } catch {
    return args;
  }
}
