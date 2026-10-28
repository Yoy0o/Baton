import { ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { Alert, Button, Empty, Input, Skeleton, Tooltip } from 'antd';
import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { api, type ToolInfo } from '../api';
import { useWorkbench } from '../context';
import { KIND_LABEL, RISK_LABEL } from '../format';

/** 工具目录：模型在本窗口能调用的全部工具（接口工具、界面工具、内置工具），按系统分组。 */
export function ToolsPanel({ active, refreshKey }: { active: boolean; refreshKey: string }) {
  const { clientId } = useWorkbench();
  const [tools, setTools] = useState<ToolInfo[] | null>(null);
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!active) return;
    let alive = true;
    setFailure(null);
    api
      .tools(clientId)
      .then((r) => {
        if (!alive) return;
        setTools(r.tools);
        setErrors(r.load_errors || {});
      })
      .catch((e: Error) => alive && setFailure(e.message));
    return () => {
      alive = false;
    };
  }, [active, clientId, refreshKey, attempt]);

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const hit = (t: ToolInfo) => !q || [t.name, t.title, t.description, t.system_title].some((s) => s?.toLowerCase().includes(q));
    const map = new Map<string, { system: string; title: string; color: string; tools: ToolInfo[] }>();
    for (const t of tools || []) {
      if (!hit(t)) continue;
      const g = map.get(t.system) || { system: t.system, title: t.system_title, color: t.color, tools: [] };
      g.tools.push(t);
      map.set(t.system, g);
    }
    return [...map.values()];
  }, [tools, query]);

  if (failure) {
    return (
      <div className="bt-panel-pad">
        <Alert
          type="error"
          showIcon
          title="工具目录加载失败"
          description={failure}
          action={
            <Button size="small" onClick={() => setAttempt(attempt + 1)}>
              重试
            </Button>
          }
        />
      </div>
    );
  }
  if (!tools) return <Skeleton active className="bt-panel-pad" paragraph={{ rows: 8 }} />;

  return (
    <div className="bt-tools">
      <div className="bt-panel-toolbar">
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索工具"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="搜索工具"
        />
        <Tooltip title="刷新">
          <Button icon={<ReloadOutlined />} onClick={() => setAttempt(attempt + 1)} aria-label="刷新工具目录" />
        </Tooltip>
      </div>
      <p className="bt-panel-note">共 {tools.length} 个工具。界面工具来自本窗口已打开的系统页面，关闭页面后不可用。</p>
      {Object.entries(errors).map(([system, list]) => (
        <Alert key={system} type="warning" showIcon className="bt-tools-error" title={`${system} 接入有问题`} description={list.join('；')} />
      ))}
      {groups.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的工具" />}
      {groups.map((g) => (
        <section key={g.system} className="bt-tool-group" style={{ '--c': g.color } as CSSProperties}>
          <h3 className="bt-tool-group-title">
            <i />
            {g.title}
            <span>{g.tools.length}</span>
          </h3>
          <ul className="bt-tool-list">
            {g.tools.map((t) => {
              const props = Object.entries(t.input_schema?.properties || {});
              const required = new Set(t.input_schema?.required || []);
              const write = t.risk === 'write' || t.risk === 'critical';
              return (
                <li key={t.name} className="bt-tool">
                  <div className="bt-tool-head">
                    <span className="bt-tool-title">{t.title || t.name}</span>
                    <span className={`bt-tool-tag ${t.kind === 'ui' ? 'is-ui' : ''}`}>{KIND_LABEL[t.kind]}</span>
                    <span className={`bt-tool-tag ${write ? 'is-write' : ''}`}>{RISK_LABEL[t.risk]}</span>
                  </div>
                  <code className="bt-tool-name">{t.name}</code>
                  <p className="bt-tool-desc">{t.description}</p>
                  {props.length > 0 && (
                    <div className="bt-tool-params" aria-label="参数">
                      {props.map(([name, p]) => (
                        <Tooltip key={name} title={p.description || undefined}>
                          <code className={required.has(name) ? 'is-required' : ''}>
                            {name}
                            {required.has(name) ? '' : '?'}
                          </code>
                        </Tooltip>
                      ))}
                    </div>
                  )}
                  {t.pairs_with && (
                    <p className="bt-tool-pair">
                      预览后经你确认，由 <code>{t.pairs_with}</code> 提交
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
