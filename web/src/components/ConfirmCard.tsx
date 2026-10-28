import { CheckCircleFilled, CloseCircleFilled, ExclamationCircleFilled, LoadingOutlined, MinusCircleFilled } from '@ant-design/icons';
import { Button } from 'antd';
import { useState } from 'react';
import { useWorkbench } from '../context';
import { useNow } from '../hooks';
import type { ConfirmInfo, StepStatus } from '../store';
import { MiniTable } from './StepChain';

function countdown(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/**
 * 写操作确认卡片：展示预览出的变更清单，用户决定后才提交。
 * commit 是对应提交步骤的状态，用来显示“已确认并提交 / 提交失败”。
 */
export function ConfirmCard({ confirm, commit }: { confirm: ConfirmInfo; commit?: StepStatus }) {
  const { decide } = useWorkbench();
  const pending = confirm.state === 'pending';
  const now = useNow(pending);
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null);
  const remain = Math.max(0, Math.round(confirm.expiresAt - now / 1000));

  const choose = (approved: boolean) => {
    setBusy(approved ? 'approve' : 'reject');
    decide(confirm.confirmId, approved);
  };

  let outcome: React.ReactNode = null;
  if (!pending) {
    if (confirm.state === 'approved') {
      outcome =
        commit === 'success' ? (
          <span className="bt-outcome is-ok">
            <CheckCircleFilled /> 已确认并提交到{confirm.systemTitle}
          </span>
        ) : commit === 'error' ? (
          <span className="bt-outcome is-bad">
            <CloseCircleFilled /> 已确认，但提交失败，详情见执行过程
          </span>
        ) : (
          <span className="bt-outcome">
            <LoadingOutlined /> 已确认，正在提交…
          </span>
        );
    } else {
      const text: string =
        (
          {
            rejected: '已取消，没有提交',
            timeout: '超时未确认，没有提交',
            stopped: '任务已停止，没有提交',
            interrupted: '服务重启，没有提交',
          } as Record<string, string>
        )[confirm.state] || '没有提交';
      outcome = (
        <span className="bt-outcome is-muted">
          <MinusCircleFilled /> {text}
        </span>
      );
    }
  }

  return (
    <section className={`bt-confirm ${pending ? 'is-pending' : 'is-done'}`} aria-label="写操作确认">
      <header className="bt-confirm-head">
        <ExclamationCircleFilled className="bt-confirm-icon" />
        <span className="bt-confirm-title">
          {pending ? '需要你确认' : '写操作'}：{confirm.title}
        </span>
        <span className="bt-confirm-sys">{confirm.systemTitle}</span>
      </header>
      {confirm.summary && <p className="bt-confirm-summary">{confirm.summary}</p>}
      {confirm.changes.length > 0 && (pending || confirm.state === 'approved') && (
        <MiniTable rows={confirm.changes} columns={confirm.columns} maxHeight={pending ? 240 : 140} />
      )}
      <footer className="bt-confirm-foot">
        {pending ? (
          <>
            <Button
              type="primary"
              className="bt-btn-write"
              loading={busy === 'approve'}
              disabled={busy !== null || remain === 0}
              onClick={() => choose(true)}
            >
              确认提交
            </Button>
            <Button disabled={busy !== null} onClick={() => choose(false)}>
              取消
            </Button>
            <span className="bt-confirm-note">
              确认前不会修改{confirm.systemTitle}
              {remain > 0 ? `，${countdown(remain)} 后失效` : '，已失效'}
            </span>
          </>
        ) : (
          outcome
        )}
      </footer>
    </section>
  );
}
