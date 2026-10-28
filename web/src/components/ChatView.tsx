import { ArrowDownOutlined, FormOutlined, LayoutOutlined, MenuUnfoldOutlined } from '@ant-design/icons';
import { Bubble, type BubbleItemType, type BubbleListProps } from '@ant-design/x';
import type { BubbleListRef } from '@ant-design/x/es/bubble/interface';
import { Alert, Badge, Button, Tooltip } from 'antd';
import { useMemo, useRef, useState } from 'react';
import type { ConvView, Run } from '../store';
import { Composer } from './Composer';
import { LogoMark } from './Logo';
import { RunMessage } from './RunMessage';
import { ModeTag, SystemsButton } from './SystemsButton';
import { WelcomeScreen } from './WelcomeScreen';

interface ChatViewProps {
  title: string;
  view: ConvView | undefined;
  loading: boolean;
  connected: boolean;
  disconnected: boolean;
  running: boolean;
  workspaceOpen: boolean;
  workspaceDot: boolean;
  showMenu: boolean;
  onMenu: () => void;
  onNew: () => void;
  onToggleWorkspace: () => void;
  onSend: (text: string) => void;
  onStop: () => void;
}

type AiContent = { run: Run; isLast: boolean };

const ROLES: BubbleListProps['role'] = {
  user: {
    placement: 'end',
    variant: 'filled',
    shape: 'corner',
    classNames: { content: 'bt-user-bubble' },
  },
  ai: {
    placement: 'start',
    variant: 'borderless',
    avatar: <LogoMark size={28} />,
    classNames: { root: 'bt-ai-root', body: 'bt-ai-body', content: 'bt-ai-content' },
    loadingRender: () => (
      <div className="bt-working" role="status">
        <span className="bt-working-dots" aria-hidden>
          <i />
          <i />
          <i />
        </span>
        已发送，正在启动
      </div>
    ),
    contentRender: (content: AiContent) => <RunMessage run={content.run} isLast={content.isLast} />,
  },
};

export function ChatView(props: ChatViewProps) {
  const { title, view, loading, connected, disconnected, running, workspaceOpen, workspaceDot, showMenu, onMenu, onNew, onToggleWorkspace, onSend, onStop } = props;
  const listRef = useRef<BubbleListRef>(null);
  const [awayFromBottom, setAwayFromBottom] = useState(false);
  const runs = view?.runs || [];
  const last = runs.at(-1);

  const items = useMemo<BubbleItemType[]>(
    () =>
      runs.flatMap((run, i) => [
        { key: `${run.runId}:u`, role: 'user', content: run.text },
        {
          key: run.runId,
          role: 'ai',
          content: { run, isLast: i === runs.length - 1 } satisfies AiContent,
          loading: Boolean(run.placeholder),
        },
      ]),
    [runs],
  );

  return (
    <section className="bt-chat" aria-label="对话">
      <header className="bt-chat-header">
        {showMenu && (
          <>
            <Tooltip title="对话列表">
              <Button type="text" icon={<MenuUnfoldOutlined />} onClick={onMenu} aria-label="打开对话列表" />
            </Tooltip>
            <Tooltip title="新对话">
              <Button type="text" icon={<FormOutlined />} onClick={onNew} aria-label="新对话" />
            </Tooltip>
          </>
        )}
        <h1 className="bt-chat-title" title={title}>
          {title}
        </h1>
        <div className="bt-chat-tools">
          <ModeTag />
          <SystemsButton />
          <Tooltip title={workspaceOpen ? '收起工作区' : workspaceDot ? '工作区有新内容' : '打开工作区（系统页面、结果、工具、审计）'}>
            <Badge dot={workspaceDot && !workspaceOpen} offset={[-4, 4]}>
              <Button
                type="text"
                className={`bt-ws-toggle ${workspaceOpen ? 'is-on' : ''}`}
                icon={<LayoutOutlined />}
                onClick={onToggleWorkspace}
                aria-label={workspaceOpen ? '收起工作区' : '打开工作区'}
                aria-pressed={workspaceOpen}
              >
                <span className="bt-hide-sm">工作区</span>
              </Button>
            </Badge>
          </Tooltip>
        </div>
      </header>

      {disconnected && <Alert type="warning" banner showIcon title="与服务的连接断开了，正在自动重连。网页断线不影响服务端正在执行的任务。" />}

      <div className="bt-chat-body">
        {runs.length === 0 ? (
          loading ? (
            <div className="bt-loading">正在打开对话…</div>
          ) : (
            <WelcomeScreen onPick={onSend} />
          )
        ) : (
          <Bubble.List
            ref={listRef}
            className="bt-messages"
            items={items}
            role={ROLES}
            autoScroll
            onScroll={(e) => setAwayFromBottom(Math.abs((e.currentTarget as HTMLDivElement).scrollTop) > 240)}
          />
        )}
        {awayFromBottom && runs.length > 0 && (
          <Button
            className="bt-jump"
            shape="circle"
            icon={<ArrowDownOutlined />}
            aria-label="回到底部"
            onClick={() => {
              listRef.current?.scrollTo({ top: 'bottom', behavior: 'smooth' });
              setAwayFromBottom(false);
            }}
          />
        )}
      </div>

      <Composer
        running={running}
        disabled={!connected || loading}
        suggestions={last && last.status === 'completed' ? last.suggestions : []}
        onSend={onSend}
        onStop={onStop}
      />
    </section>
  );
}
