import { CloseOutlined, ExportOutlined } from '@ant-design/icons';
import { Badge, Button, Tabs, Tooltip } from 'antd';
import { memo, useMemo, type CSSProperties } from 'react';
import type { SystemInfo } from '../api';
import { bridgeTools, useWorkbench, type OpenResultArgs } from '../context';
import { AuditPanel } from './AuditPanel';
import { ResultsPanel, type ResultListItem } from './ResultsPanel';
import { ToolsPanel } from './ToolsPanel';

export const sysTab = (system: string) => `sys:${system}`;

/** 内嵌的业务系统页面。地址只在首次渲染时确定，之后不变，避免页面重新加载、丢失状态。 */
const SystemFrame = memo(function SystemFrame({ system, clientId }: { system: SystemInfo; clientId: string }) {
  const src = useMemo(() => {
    const url = new URL(system.embed_url!, location.href);
    url.searchParams.set('agent', location.origin);
    url.searchParams.set('agent_client', clientId);
    return url.toString();
  }, [system.embed_url, clientId]);
  return <iframe className="bt-frame" src={src} title={system.ui_title} />;
});

interface WorkspaceProps {
  tab: string;
  onTab: (key: string) => void;
  onClose: () => void;
  results: ResultListItem[];
  selected: OpenResultArgs | null;
  onSelectResult: (args: OpenResultArgs) => void;
  resultsDot: boolean;
  refreshKey: string;
}

/** 右侧工作区：已接入系统的页面 + 本对话的结果 + 工具目录 + 审计。 */
export function Workspace({ tab, onTab, onClose, results, selected, onSelectResult, resultsDot, refreshKey }: WorkspaceProps) {
  const { config, clientId, bridges } = useWorkbench();
  const embedded = (config?.systems || []).filter((s) => s.embed_url);
  const current = embedded.find((s) => sysTab(s.system) === tab);

  const items = [
    ...embedded.map((s) => {
      const live = bridgeTools(bridges, s.system).length > 0;
      return {
        key: sysTab(s.system),
        forceRender: true,
        label: (
          <Tooltip title={live ? '页面已接入，Agent 可以操作它' : '页面还没有接入 Agent'} mouseEnterDelay={0.6}>
            <span className="bt-tab-sys" style={{ '--c': s.color } as CSSProperties}>
              <i className={live ? 'is-live' : ''} aria-hidden />
              {s.ui_title}
            </span>
          </Tooltip>
        ),
        children: <SystemFrame system={s} clientId={clientId} />,
      };
    }),
    {
      key: 'results',
      label: (
        <Badge dot={resultsDot} offset={[4, 0]}>
          <span>结果{results.length ? <span className="bt-tab-count">{results.length}</span> : null}</span>
        </Badge>
      ),
      children: <ResultsPanel items={results} selected={selected} onSelect={onSelectResult} onShowInSystem={(s) => onTab(sysTab(s))} />,
    },
    { key: 'tools', label: '工具', children: <ToolsPanel active={tab === 'tools'} refreshKey={refreshKey} /> },
    { key: 'audit', label: '审计', children: <AuditPanel active={tab === 'audit'} refreshKey={refreshKey} /> },
  ];

  return (
    <Tabs
      className="bt-ws-tabs"
      activeKey={tab}
      onChange={onTab}
      items={items}
      tabBarExtraContent={{
        right: (
          <div className="bt-ws-extra">
            {current && (
              <Tooltip title={`在新窗口打开${current.ui_title}`}>
                <Button
                  type="text"
                  size="small"
                  icon={<ExportOutlined />}
                  href={current.embed_url!}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={`在新窗口打开${current.ui_title}`}
                />
              </Tooltip>
            )}
            <Tooltip title="收起工作区">
              <Button type="text" size="small" icon={<CloseOutlined />} onClick={onClose} aria-label="收起工作区" />
            </Tooltip>
          </div>
        ),
      }}
    />
  );
}
