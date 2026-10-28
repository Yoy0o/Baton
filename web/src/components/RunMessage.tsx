import { CheckOutlined, CopyOutlined, DislikeFilled, DislikeOutlined, LikeFilled, LikeOutlined, RedoOutlined } from '@ant-design/icons';
import { Think } from '@ant-design/x';
import { Alert, App as AntApp, Button, Tooltip } from 'antd';
import { memo, useState } from 'react';
import { copyText } from '../clipboard';
import { useWorkbench } from '../context';
import { formatDuration } from '../format';
import type { Run, ToolStep } from '../store';
import { ConfirmCard } from './ConfirmCard';
import { Markdown } from './Markdown';
import { StepChain } from './StepChain';

const STATUS_NOTE: Partial<Record<Run['status'], string>> = {
  stopped: '你停止了这次任务',
  interrupted: '服务重启，这次任务中断了',
};

/** 一次运行在对话里的呈现：思考过程、执行过程、确认卡片、答复、操作。 */
export const RunMessage = memo(function RunMessage({ run, isLast }: { run: Run; isLast: boolean }) {
  const { regenerate, feedback, setFeedback, running } = useWorkbench();
  const toolSteps = run.steps.filter((s): s is ToolStep => s.kind === 'tool');
  const reasoning = [run.reasoning, run.liveReasoning].filter(Boolean).join('\n\n');
  const thinking = run.status === 'running' && Boolean(run.liveReasoning) && !run.liveText;
  const answer = run.answer ?? run.liveText;
  const streaming = run.status === 'running' && run.answer === undefined && Boolean(run.liveText);
  const waiting = run.confirms.some((c) => c.state === 'pending');
  const busy = run.status === 'running' && !answer && !waiting && !thinking;
  const finished = run.status !== 'running';
  const canRegenerate = isLast && finished && !running;
  const liked = feedback[run.runId] === 'like';
  const disliked = feedback[run.runId] === 'dislike';

  return (
    <div className="bt-run">
      {reasoning && (
        <Think title={thinking ? '正在思考' : '思考过程'} loading={thinking} blink={thinking} defaultExpanded={false}>
          <div className="bt-reasoning">{reasoning}</div>
        </Think>
      )}

      {run.steps.length > 0 && <StepChain run={run} />}

      {run.confirms.map((c) => (
        <ConfirmCard key={c.confirmId} confirm={c} commit={toolSteps.filter((s) => s.tool === c.tool).at(-1)?.status} />
      ))}

      {answer ? (
        <div className={`bt-answer ${run.partial ? 'is-partial' : ''}`}>
          <Markdown content={answer} streaming={streaming} />
        </div>
      ) : null}

      {busy && (
        <div className="bt-working" role="status">
          <span className="bt-working-dots" aria-hidden>
            <i />
            <i />
            <i />
          </span>
          {toolSteps.length ? '正在处理工具结果' : '正在理解你的任务'}
        </div>
      )}

      {run.error && (
        <Alert
          type="error"
          showIcon
          title="这次任务没有完成"
          description={run.error}
          action={
            canRegenerate ? (
              <Button size="small" onClick={regenerate}>
                重试
              </Button>
            ) : undefined
          }
        />
      )}

      {STATUS_NOTE[run.status] && <div className="bt-run-note">{STATUS_NOTE[run.status]}</div>}

      {finished && (
        <div className="bt-run-foot">
          <div className="bt-run-actions" role="group" aria-label="对这条回复">
            {answer && <CopyButton text={answer} />}
            {canRegenerate && (
              <Tooltip title="重新生成（写操作仍会先请你确认）">
                <Button type="text" size="small" icon={<RedoOutlined />} onClick={regenerate} aria-label="重新生成" />
              </Tooltip>
            )}
            <Tooltip title="有帮助">
              <Button
                type="text"
                size="small"
                className={liked ? 'is-active' : ''}
                icon={liked ? <LikeFilled /> : <LikeOutlined />}
                aria-label="有帮助"
                aria-pressed={liked}
                onClick={() => setFeedback(run.runId, liked ? null : 'like')}
              />
            </Tooltip>
            <Tooltip title="没帮助">
              <Button
                type="text"
                size="small"
                className={disliked ? 'is-active' : ''}
                icon={disliked ? <DislikeFilled /> : <DislikeOutlined />}
                aria-label="没帮助"
                aria-pressed={disliked}
                onClick={() => setFeedback(run.runId, disliked ? null : 'dislike')}
              />
            </Tooltip>
          </div>
          {run.durationMs !== undefined && <span className="bt-run-time">用时 {formatDuration(run.durationMs)}</span>}
        </div>
      )}
    </div>
  );
});

function CopyButton({ text }: { text: string }) {
  const { message } = AntApp.useApp();
  const [done, setDone] = useState(false);
  return (
    <Tooltip title={done ? '已复制' : '复制'}>
      <Button
        type="text"
        size="small"
        icon={done ? <CheckOutlined /> : <CopyOutlined />}
        aria-label="复制回复"
        onClick={async () => {
          if (await copyText(text)) {
            setDone(true);
            window.setTimeout(() => setDone(false), 1500);
          } else {
            message.error('复制失败，请手动选择文字复制');
          }
        }}
      />
    </Tooltip>
  );
}
