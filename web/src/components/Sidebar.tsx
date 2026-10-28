import {
  DeleteOutlined,
  DesktopOutlined,
  EditOutlined,
  LoadingOutlined,
  MenuFoldOutlined,
  MoonOutlined,
  PlusOutlined,
  SearchOutlined,
  SunOutlined,
} from '@ant-design/icons';
import { Conversations, type ConversationItemType } from '@ant-design/x';
import { Button, Input, Modal, Segmented, Tooltip } from 'antd';
import { useMemo, useState } from 'react';
import type { ConversationMeta } from '../api';
import { TIME_GROUP_ORDER, timeGroup } from '../format';
import { LogoMark } from './Logo';

export type ThemeMode = 'light' | 'dark' | 'system';

interface SidebarProps {
  appName: string;
  tagline: string;
  version: string;
  conversations: ConversationMeta[];
  activeId: string | null;
  connected: boolean;
  themeMode: ThemeMode;
  onThemeMode: (mode: ThemeMode) => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => Promise<void>;
  onDelete: (id: string) => void;
  onCollapse?: () => void;
}

export function Sidebar(props: SidebarProps) {
  const { appName, tagline, version, conversations, activeId, connected, themeMode, onThemeMode, onNew, onSelect, onRename, onDelete, onCollapse } =
    props;
  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();

  const items = useMemo<ConversationItemType[]>(() => {
    const sorted = conversations.filter((c) => !q || c.title.toLowerCase().includes(q)).sort(
      (a, b) => TIME_GROUP_ORDER.indexOf(timeGroup(a.updated_at)) - TIME_GROUP_ORDER.indexOf(timeGroup(b.updated_at)) || b.updated_at - a.updated_at,
    );
    return sorted.map((c) => ({
      key: c.id,
      group: timeGroup(c.updated_at),
      title: c.title,
      tabIndex: 0,
      'aria-current': c.id === activeId ? 'page' : undefined,
      onKeyDown: (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect(c.id);
        }
      },
      label: (
        <span className="bt-conv-label">
          <span className="bt-conv-title">{c.title}</span>
          {c.pending_confirm ? (
            <span className="bt-conv-badge is-write">待确认</span>
          ) : c.running ? (
            <LoadingOutlined className="bt-conv-spin" aria-label="执行中" />
          ) : null}
        </span>
      ),
    }));
  }, [conversations, activeId, onSelect, q]);

  const submitRename = async () => {
    if (!renaming || !renaming.title.trim()) return;
    setSaving(true);
    try {
      await onRename(renaming.id, renaming.title.trim());
      setRenaming(null);
    } finally {
      setSaving(false);
    }
  };

  return (
    <nav className="bt-sidebar" aria-label="对话列表">
      <div className="bt-brand">
        <LogoMark size={30} />
        <div className="bt-brand-text">
          <span className="bt-brand-name">{appName}</span>
          <span className="bt-brand-tagline">{tagline}</span>
        </div>
        {onCollapse && (
          <Tooltip title="收起侧栏" placement="right">
            <Button type="text" icon={<MenuFoldOutlined />} onClick={onCollapse} aria-label="收起侧栏" />
          </Tooltip>
        )}
      </div>

      {conversations.length > 6 && (
        <div className="bt-conv-search">
          <Input
            size="small"
            variant="filled"
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索对话"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="搜索对话"
          />
        </div>
      )}

      <div className="bt-conv-scroll">
        <Conversations
          className="bt-conversations"
          items={items}
          activeKey={activeId ?? undefined}
          onActiveChange={(key) => onSelect(String(key))}
          groupable
          creation={{ label: '新对话', icon: <PlusOutlined />, onClick: onNew, align: 'start' }}
          menu={(conv) => ({
            items: [
              { key: 'rename', label: '重命名', icon: <EditOutlined /> },
              { type: 'divider' as const },
              { key: 'delete', label: '删除', icon: <DeleteOutlined />, danger: true },
            ],
            onClick: ({ key, domEvent }) => {
              domEvent.stopPropagation();
              const meta = conversations.find((c) => c.id === conv.key);
              if (key === 'rename') setRenaming({ id: String(conv.key), title: meta?.title || '' });
              if (key === 'delete') onDelete(String(conv.key));
            },
          })}
        />
        {conversations.length === 0 && <p className="bt-conv-empty">还没有对话。发出第一个任务后，会出现在这里。</p>}
        {conversations.length > 0 && items.length === 0 && <p className="bt-conv-empty">没有标题包含“{query.trim()}”的对话。</p>}
      </div>

      <footer className="bt-sidebar-foot">
        <span className={`bt-conn ${connected ? 'is-on' : 'is-off'}`} role="status">
          <i aria-hidden />
          {connected ? '已连接服务' : '正在重连服务'}
        </span>
        <Segmented
          size="small"
          value={themeMode}
          onChange={(v) => onThemeMode(v as ThemeMode)}
          options={[
            { value: 'light', icon: <SunOutlined />, title: '浅色' },
            { value: 'dark', icon: <MoonOutlined />, title: '深色' },
            { value: 'system', icon: <DesktopOutlined />, title: '跟随系统' },
          ]}
          aria-label="界面主题"
        />
        <span className="bt-version">
          {appName} {version}
        </span>
      </footer>

      <Modal
        title="重命名对话"
        open={renaming !== null}
        onCancel={() => setRenaming(null)}
        onOk={submitRename}
        okText="保存"
        confirmLoading={saving}
        okButtonProps={{ disabled: !renaming?.title.trim() }}
        destroyOnHidden
      >
        <Input
          autoFocus
          maxLength={40}
          showCount
          value={renaming?.title}
          onChange={(e) => renaming && setRenaming({ ...renaming, title: e.target.value })}
          onPressEnter={submitRename}
          aria-label="对话标题"
        />
      </Modal>
    </nav>
  );
}
