import { CopyOutlined, DownloadOutlined, EnvironmentOutlined, PartitionOutlined, ReloadOutlined, TableOutlined } from '@ant-design/icons';
import { Alert, App as AntApp, Button, Dropdown, Empty, Select, Skeleton, Table, Tooltip } from 'antd';
import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { api, resultGeoJsonUrl, type ResultFull, type Row } from '../api';
import { copyText } from '../clipboard';
import { bridgeTools, useWorkbench, type OpenResultArgs } from '../context';
import { download, formatCell, formatDateTime, toCsv } from '../format';
import { categoricalFields, colorMap, PointMap, pointsOf } from './PointMap';
import { FIELD_LABELS } from './StepChain';

export interface ResultListItem {
  resultId: string;
  resultType?: string;
  summary?: string;
  total?: number;
  tool: string;
  color: string;
  systemTitle: string;
}

const TYPE_LABEL: Record<string, string> = {
  table: '表格',
  feature_collection: '点位',
  group_plan: '分组方案',
  object: '对象',
};

const TYPE_ICON: Record<string, React.ReactNode> = {
  table: <TableOutlined />,
  feature_collection: <EnvironmentOutlined />,
  group_plan: <PartitionOutlined />,
};

/** 结果不可变，取过一次就缓存。 */
const cache = new Map<string, ResultFull>();

function defaultColorBy(result: ResultFull, rows: Row[]): string | null {
  const fields = result.fields.length ? result.fields : Object.keys(rows[0] || {});
  const candidates = categoricalFields(rows.slice(0, 500), fields);
  for (const f of ['group', 'customer', 'status']) if (candidates.includes(f)) return f;
  return candidates[0] ?? null;
}

interface ResultsPanelProps {
  items: ResultListItem[];
  selected: OpenResultArgs | null;
  onSelect: (args: OpenResultArgs) => void;
  onShowInSystem: (system: string) => void;
}

export function ResultsPanel({ items, selected, onSelect, onShowInSystem }: ResultsPanelProps) {
  const { config, clientId, conversationId, bridges } = useWorkbench();
  const { message } = AntApp.useApp();
  const current = selected ?? (items.length ? { resultId: items[items.length - 1].resultId } : null);
  const resultId = current?.resultId ?? null;
  const [result, setResult] = useState<ResultFull | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [colorBy, setColorBy] = useState<string | null>(null);
  const [showing, setShowing] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!resultId) {
      setResult(null);
      return;
    }
    const hit = cache.get(resultId);
    setError(null);
    if (hit) {
      setResult(hit);
      return;
    }
    setResult(null);
    let alive = true;
    api
      .result(resultId)
      .then((r) => {
        cache.set(resultId, r);
        if (alive) setResult(r);
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [resultId, attempt]);

  const rows = result?.rows ?? [];
  const points = useMemo(() => pointsOf(rows), [rows]);
  const fields = useMemo(() => (result?.fields.length ? result.fields : [...new Set(rows.slice(0, 50).flatMap((r) => Object.keys(r)))]), [result, rows]);
  const colorFields = useMemo(() => categoricalFields(rows.slice(0, 500), fields), [rows, fields]);

  // 切换结果时：用打开时指定的着色字段，否则按字段猜一个
  useEffect(() => {
    if (!result) return;
    const wanted = current?.colorBy;
    setColorBy(wanted && fields.includes(wanted) ? wanted : defaultColorBy(result, rows));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, current?.colorBy]);

  const layerSystems = useMemo(
    () => (config?.systems || []).filter((s) => s.ui_tools.some((t) => t.tool === 'show_result_layer')),
    [config],
  );
  const showTargets = useMemo(
    () => layerSystems.filter((s) => bridgeTools(bridges, s.system).includes('show_result_layer')),
    [layerSystems, bridges],
  );

  const options = useMemo(() => {
    const list = [...items];
    if (resultId && !list.some((i) => i.resultId === resultId)) list.push({ resultId, tool: '', color: '#64748B', systemTitle: '', summary: result?.summary });
    return list
      .slice()
      .reverse()
      .map((i) => ({
        value: i.resultId,
        label: (
          <span className="bt-result-opt" style={{ '--c': i.color } as CSSProperties}>
            {(i.resultType && TYPE_ICON[i.resultType]) || <TableOutlined />}
            <code>{i.resultId}</code>
            <span className="bt-result-opt-text">{i.summary || TYPE_LABEL[i.resultType || ''] || ''}</span>
          </span>
        ),
      }));
  }, [items, resultId, result?.summary]);

  if (!resultId) {
    return (
      <div className="bt-panel-empty">
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <>
              <div className="bt-empty-title">这次对话还没有结果</div>
              <div className="bt-empty-text">让 Agent 查询或计算后，结果会出现在这里。点对话里的结果编号也能直接打开。</div>
            </>
          }
        />
      </div>
    );
  }

  const spatial = points.length > 0;
  const showIn = async (system: string) => {
    setShowing(system);
    try {
      const r = await api.uiAction(clientId, conversationId, `${system}__show_result_layer`, {
        result_id: resultId,
        ...(colorBy ? { color_by: colorBy } : {}),
      });
      if (r.ok) {
        message.success(r.summary || '已显示');
        onShowInSystem(system);
      } else {
        message.error(r.message || '显示失败');
      }
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setShowing(null);
    }
  };

  const copyId = async () => {
    if (await copyText(resultId)) message.success('已复制结果编号');
    else message.error('复制失败，请手动选择编号复制');
  };

  const downloadGeoJson = async () => {
    try {
      const resp = await fetch(resultGeoJsonUrl(resultId));
      if (!resp.ok) throw new Error(String(resp.status));
      download(`${resultId}.geojson`, await resp.text(), 'application/geo+json');
    } catch {
      message.error('GeoJSON 下载失败');
    }
  };

  const groupColors = result?.result_type === 'group_plan' ? colorMap(rows.map((r) => r.group ?? '')) : null;

  return (
    <div className="bt-results">
      <div className="bt-results-bar">
        <Select
          className="bt-results-select"
          value={resultId}
          options={options}
          onChange={(v) => onSelect({ resultId: v })}
          popupMatchSelectWidth={false}
          aria-label="选择结果"
        />
      </div>

      {error ? (
        <Alert
          type="warning"
          showIcon
          title="这个结果打不开"
          description={error}
          action={
            <Button size="small" icon={<ReloadOutlined />} onClick={() => setAttempt(attempt + 1)}>
              重试
            </Button>
          }
        />
      ) : !result ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : (
        <>
          <header className="bt-result-head">
            <h2 className="bt-result-summary">{current?.title || result.summary}</h2>
            <div className="bt-result-meta">
              <span className="bt-result-type">
                {TYPE_ICON[result.result_type] || <TableOutlined />}
                {TYPE_LABEL[result.result_type] || result.result_type}
              </span>
              {result.total !== null && <span>{result.total} 行</span>}
              {result.source?.tool && <code>{result.source.tool}</code>}
              <span>{formatDateTime(result.created_at)}</span>
            </div>
            <div className="bt-result-actions">
              {spatial &&
                showTargets.map((s) => (
                  <Button
                    key={s.system}
                    type="primary"
                    icon={<EnvironmentOutlined />}
                    loading={showing === s.system}
                    onClick={() => showIn(s.system)}
                  >
                    在{s.ui_title}显示
                  </Button>
                ))}
              <Dropdown
                trigger={['click']}
                menu={{
                  items: [
                    { key: 'csv', label: '表格（CSV）' },
                    ...(spatial ? [{ key: 'geojson', label: '地理数据（GeoJSON）' }] : []),
                  ],
                  onClick: ({ key }) =>
                    key === 'csv' ? download(`${resultId}.csv`, toCsv(rows), 'text/csv;charset=utf-8') : downloadGeoJson(),
                }}
              >
                <Button icon={<DownloadOutlined />}>下载</Button>
              </Dropdown>
              <Tooltip title="复制结果编号，可以在对话里引用">
                <Button icon={<CopyOutlined />} onClick={copyId} aria-label="复制结果编号" />
              </Tooltip>
            </div>
            {spatial && showTargets.length === 0 && layerSystems.length > 0 && (
              <p className="bt-result-hint">{layerSystems.map((s) => s.ui_title).join('、')}页面接入后，可以把这个结果叠加到业务地图上。</p>
            )}
          </header>

          {spatial && (
            <section className="bt-result-section" aria-label="点位预览">
              <div className="bt-section-head">
                <h3>点位预览</h3>
                {colorFields.length > 0 && (
                  <Select
                    size="small"
                    value={colorBy ?? '__none__'}
                    onChange={(v) => setColorBy(v === '__none__' ? null : v)}
                    options={[{ value: '__none__', label: '不着色' }, ...colorFields.map((f) => ({ value: f, label: `按${FIELD_LABELS[f] || f}着色` }))]}
                    popupMatchSelectWidth={false}
                    aria-label="着色字段"
                  />
                )}
              </div>
              <PointMap
                points={points}
                colorBy={colorBy}
                groups={result.data.groups}
                idField={String(result.data.id_field || 'driver_id')}
              />
            </section>
          )}

          {result.data.groups && result.data.groups.length > 0 && (
            <section className="bt-result-section" aria-label="分组统计">
              <div className="bt-section-head">
                <h3>分组统计</h3>
              </div>
              <Table
                size="small"
                pagination={false}
                rowKey="group_no"
                dataSource={result.data.groups}
                columns={[
                  {
                    key: 'g',
                    title: '组',
                    dataIndex: 'group_no',
                    render: (g: number) => (
                      <span className="bt-group-no" style={{ '--c': groupColors?.get(String(g)) || '#5f6b78' } as CSSProperties}>
                        <i />
                        {g}
                      </span>
                    ),
                  },
                  { key: 'size', title: '人数', dataIndex: 'size', align: 'right' },
                  { key: 'avg', title: '平均距中心', dataIndex: 'avg_dist_km', align: 'right', render: (v: number) => `${v} km` },
                  { key: 'max', title: '最远', dataIndex: 'max_dist_km', align: 'right', render: (v: number) => `${v} km` },
                ]}
              />
            </section>
          )}

          <section className="bt-result-section" aria-label="数据">
            <div className="bt-section-head">
              <h3>数据</h3>
              <span className="bt-section-note">
                {rows.length < (result.total ?? 0) ? `显示前 ${rows.length} 行，共 ${result.total} 行` : `${rows.length} 行`}
              </span>
            </div>
            <Table
              size="small"
              className="bt-data-table"
              rowKey={(_, i) => String(i)}
              dataSource={rows}
              scroll={{ x: 'max-content' }}
              pagination={rows.length > 20 ? { pageSize: 20, size: 'small', showSizeChanger: false } : false}
              columns={fields.map((f) => ({
                key: f,
                dataIndex: f,
                title: FIELD_LABELS[f] || f,
                ellipsis: true,
                render: (v: unknown) => formatCell(v),
              }))}
            />
          </section>
        </>
      )}
    </div>
  );
}
