import {
  CheckOutlined,
  CloseOutlined,
  DownOutlined,
  LoadingOutlined,
  MessageOutlined,
  MinusOutlined,
  PauseOutlined,
} from '@ant-design/icons';
import { ThoughtChain, type ThoughtChainItemType } from '@ant-design/x';
import { Table, Tag } from 'antd';
import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import type { Row } from '../api';
import { KIND_LABEL, RISK_LABEL, formatCell, formatDuration } from '../format';
import type { Run, StepStatus, ToolStep } from '../store';
import { Markdown } from './Markdown';
import { LinkedText, ResultChip } from './ResultChip';

const STATUS_ICON: Record<StepStatus, React.ReactNode> = {
  running: <LoadingOutlined />,
  waiting: <PauseOutlined />,
  success: <CheckOutlined />,
  error: <CloseOutlined />,
  cancelled: <MinusOutlined />,
};

const STATUS_TEXT: Record<StepStatus, string> = {
  running: '执行中',
  waiting: '等待确认',
  success: '已完成',
  error: '失败',
  cancelled: '已停止',
};

/**
 * 执行步骤的“站点”：环的颜色是系统标识色，中间是状态。
 * 站点下面的线段也用这个颜色，整条执行过程读起来像一条换乘线路：颜色变了，就是换到了另一个系统。
 */
function Station({ status, color }: { status: StepStatus; color: string }) {
  return (
    <span className={`bt-station is-${status}`} style={{ '--c': color } as CSSProperties} role="img" aria-label={STATUS_TEXT[status]}>
      <i className="bt-station-line" aria-hidden />
      {STATUS_ICON[status]}
    </span>
  );
}

/** 折叠时的线路缩略：每个点是一次工具调用，颜色对应系统。 */
export function RouteDots({ steps, max = 14 }: { steps: ToolStep[]; max?: number }) {
  const shown = steps.slice(0, max);
  return (
    <span className="bt-route" aria-hidden>
      {shown.map((s, i) => (
        <span key={s.callId} className="bt-route-seg">
          {i > 0 && <b />}
          <i className={`is-${s.status}`} style={{ '--c': s.color } as CSSProperties} />
        </span>
      ))}
      {steps.length > max && <em>+{steps.length - max}</em>}
    </span>
  );
}

export const FIELD_LABELS: Record<string, string> = {
  driver_id: '司机 ID',
  name: '姓名',
  from_group: '原组',
  to_group: '新组',
  group: '组号',
  customer: '客户',
  asset_id: '资产',
  duration_min: '停留（分钟）',
  lng: '经度',
  lat: '纬度',
};

export function MiniTable({ rows, columns, maxHeight = 220 }: { rows: Row[]; columns?: string[]; maxHeight?: number }) {
  const cols = columns?.length ? columns : [...new Set(rows.slice(0, 30).flatMap((r) => Object.keys(r)))];
  return (
    <Table
      size="small"
      className="bt-mini-table"
      pagination={false}
      scroll={{ x: 'max-content', y: maxHeight }}
      rowKey={(_, i) => String(i)}
      dataSource={rows}
      columns={cols.map((c) => ({
        key: c,
        dataIndex: c,
        title: FIELD_LABELS[c] || c,
        render: (v: unknown) => formatCell(v),
        ellipsis: true,
      }))}
    />
  );
}

function StepDetails({ step }: { step: ToolStep }) {
  const args = JSON.stringify(step.args, null, 2);
  return (
    <div className="bt-step-detail">
      <div className="bt-step-meta">
        <code>{step.tool}</code>
        {step.toolKind && <span>{KIND_LABEL[step.toolKind]}</span>}
        {step.risk && <span>{RISK_LABEL[step.risk]}</span>}
        {step.durationMs !== undefined && <span>{formatDuration(step.durationMs)}</span>}
      </div>
      {args !== '{}' && (
        <>
          <div className="bt-detail-label">参数</div>
          <pre className="bt-pre">{args}</pre>
        </>
      )}
      {step.status === 'error' && step.fixHint && (
        <>
          <div className="bt-detail-label">修正提示（已交给模型）</div>
          <p className="bt-detail-text">{step.fixHint}</p>
        </>
      )}
      {step.changes && step.changes.length > 0 ? (
        <>
          <div className="bt-detail-label">变更清单（{step.changes.length} 条，预览未落库）</div>
          <MiniTable rows={step.changes} columns={step.columns} />
        </>
      ) : step.preview && step.preview.length > 0 ? (
        <>
          <div className="bt-detail-label">预览前 {step.preview.length} 行{step.total ? `，共 ${step.total} 行` : ''}</div>
          <MiniTable rows={step.preview} />
        </>
      ) : null}
    </div>
  );
}

function describe(step: ToolStep): React.ReactNode {
  switch (step.status) {
    case 'running':
      return <span className="bt-step-desc is-muted">执行中…</span>;
    case 'waiting':
      return <span className="bt-step-desc is-write">等待你确认</span>;
    case 'cancelled':
      return <span className="bt-step-desc is-muted">已停止，未执行完</span>;
    case 'error':
      return (
        <span className="bt-step-desc is-error">
          {step.message || '调用失败'}
          {step.errorCode ? <code className="bt-err-code">{step.errorCode}</code> : null}
        </span>
      );
    default:
      return (
        <span className="bt-step-desc">
          <span>
            <LinkedText text={step.summary || ''} />
          </span>
          {step.resultId && !step.summary?.includes(step.resultId) && <ResultChip id={step.resultId} type={step.resultType} />}
        </span>
      );
  }
}

export function StepChain({ run }: { run: Run }) {
  const toolSteps = run.steps.filter((s): s is ToolStep => s.kind === 'tool');
  const problem = toolSteps.some((s) => s.status === 'error') || run.status === 'error';
  const waiting = toolSteps.some((s) => s.status === 'waiting');
  const autoOpen = run.status === 'running' || problem || waiting;
  const [open, setOpen] = useState(autoOpen);
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (!touched) setOpen(autoOpen);
  }, [autoOpen, touched]);

  const items = useMemo<ThoughtChainItemType[]>(
    () =>
      run.steps.map((s) => {
        if (s.kind === 'note') {
          return {
            key: s.id,
            icon: (
              <span className="bt-station is-note" role="img" aria-label="说明">
                <i className="bt-station-line" aria-hidden />
                <MessageOutlined />
              </span>
            ),
            title: <span className="bt-step-name is-note">模型说明</span>,
            description: <Markdown content={s.text} />,
          };
        }
        const write = s.risk === 'write' || s.risk === 'critical';
        return {
          key: s.callId,
          icon: <Station status={s.status} color={s.color} />,
          title: (
            <span className="bt-step-title">
              <span className="bt-step-name">{s.title}</span>
              <span className="bt-sys" style={{ '--c': s.color } as CSSProperties}>
                {s.systemTitle}
              </span>
              {s.toolKind === 'ui' && <span className="bt-kind">界面</span>}
              {write && (
                <Tag className="bt-write-tag" variant="filled">
                  写入
                </Tag>
              )}
            </span>
          ),
          description: describe(s),
          content: <StepDetails step={s} />,
          collapsible: true,
        };
      }),
    [run.steps],
  );

  const done = toolSteps.filter((s) => s.status === 'success').length;
  const head =
    run.status === 'running'
      ? waiting
        ? `等待你确认，已执行 ${done} 步`
        : `正在执行第 ${toolSteps.length} 步`
      : run.status === 'stopped'
        ? `已停止，执行了 ${toolSteps.length} 步`
        : run.status === 'interrupted'
          ? `任务中断，执行了 ${toolSteps.length} 步`
          : `执行了 ${toolSteps.length} 步${run.durationMs !== undefined ? `，用时 ${formatDuration(run.durationMs)}` : ''}`;

  return (
    <div className={`bt-steps ${open ? 'is-open' : ''}`}>
      <button
        type="button"
        className="bt-steps-head"
        aria-expanded={open}
        onClick={() => {
          setTouched(true);
          setOpen(!open);
        }}
      >
        <span className="bt-steps-label">执行过程</span>
        <RouteDots steps={toolSteps} />
        <span className="bt-steps-sum">{head}</span>
        <DownOutlined className="bt-steps-caret" rotate={open ? 180 : 0} />
      </button>
      {open && (
        <div className="bt-steps-body">
          <ThoughtChain items={items} line="solid" />
        </div>
      )}
    </div>
  );
}
