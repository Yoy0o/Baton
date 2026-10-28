import { useMemo, useState, type CSSProperties } from 'react';
import type { GroupStat, Row } from '../api';
import { formatCell } from '../format';

/** 与车队地图台相同的分类色板和排序规则，同一个结果在两边颜色一致。 */
export const PALETTE = ['#2a6fdb', '#e8710a', '#1f9d55', '#d63a3a', '#8a4fd6', '#8c5a3c', '#d6489c', '#139bb3', '#a3a31a', '#5f6b78'];
const NEUTRAL = '#5f6b78';

export function colorMap(values: unknown[]): Map<string, string> {
  const uniq = [...new Set(values.map((v) => String(v)))].sort((a, b) => (+a && +b ? +a - +b : a.localeCompare(b, 'zh')));
  return new Map(uniq.map((v, i) => [v, PALETTE[i % PALETTE.length]]));
}

export interface MapPoint {
  lng: number;
  lat: number;
  props: Row;
}

/** 从结果的行里取出点位（行里有 lng/lat 或 lon/longitude、latitude）。 */
export function pointsOf(rows: Row[]): MapPoint[] {
  const pick = (r: Row, keys: string[]) => {
    for (const k of keys) if (typeof r[k] === 'number') return r[k] as number;
    return undefined;
  };
  const out: MapPoint[] = [];
  for (const r of rows) {
    const lng = pick(r, ['lng', 'lon', 'longitude']);
    const lat = pick(r, ['lat', 'latitude']);
    if (lng !== undefined && lat !== undefined) out.push({ lng, lat, props: r });
  }
  return out;
}

/** 适合用来着色的字段：取值 2 到 24 种。 */
export function categoricalFields(rows: Row[], fields: string[]): string[] {
  return fields.filter((f) => {
    if (['lng', 'lat', 'lon', 'longitude', 'latitude'].includes(f)) return false;
    const vals = new Set<string>();
    for (const r of rows) {
      const v = r[f];
      if (v === undefined || v === null || typeof v === 'object') continue;
      vals.add(String(v));
      if (vals.size > 24) return false;
    }
    return vals.size >= 2;
  });
}

interface PointMapProps {
  points: MapPoint[];
  colorBy?: string | null;
  groups?: GroupStat[];
  idField?: string;
  label?: string;
}

/**
 * 结果预览用的轻量点位图：不加载底图，只画点和分组中心。
 * 完整的地图交互在车队地图台里（“在车队地图显示”）。
 */
export function PointMap({ points, colorBy, groups, idField = 'driver_id', label }: PointMapProps) {
  const [focus, setFocus] = useState<string | null>(null);

  const geo = useMemo(() => {
    if (!points.length) return null;
    const meanLat = points.reduce((s, p) => s + p.lat, 0) / points.length;
    const k = Math.cos((meanLat * Math.PI) / 180);
    const xs = points.map((p) => p.lng * k);
    const ys = points.map((p) => -p.lat);
    let [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    let w = Math.max(x1 - x0, 1e-3);
    let h = Math.max(y1 - y0, 1e-3);
    // 控制长宽比，避免点位呈一条线时图过扁或过高
    const ratio = w / h;
    if (ratio > 2.2) {
      const nh = w / 2.2;
      y0 -= (nh - h) / 2;
      h = nh;
    } else if (ratio < 0.75) {
      const nw = h * 0.75;
      x0 -= (nw - w) / 2;
      w = nw;
    }
    const pad = Math.max(w, h) * 0.07;
    x0 -= pad;
    y0 -= pad;
    w += pad * 2;
    h += pad * 2;
    x1 = x0 + w;
    y1 = y0 + h;
    return { k, x0, y0, w, h, x1, y1 };
  }, [points]);

  const colors = useMemo(() => (colorBy ? colorMap(points.map((p) => p.props[colorBy] ?? '')) : null), [points, colorBy]);
  const counts = useMemo(() => {
    const m = new Map<string, number>();
    if (colorBy) for (const p of points) m.set(String(p.props[colorBy] ?? ''), (m.get(String(p.props[colorBy] ?? '')) || 0) + 1);
    return m;
  }, [points, colorBy]);

  if (!geo) return null;
  const r = geo.w / 140;
  const showGroups = Boolean(groups?.length) && colorBy === 'group';

  return (
    <figure className="bt-pointmap" aria-label={label || '点位预览'}>
      <svg viewBox={`${geo.x0} ${geo.y0} ${geo.w} ${geo.h}`} role="img" aria-label={`${points.length} 个点位`}>
        {points.map((p, i) => {
          const value = colorBy ? String(p.props[colorBy] ?? '') : '';
          const dim = focus !== null && value !== focus;
          const name = [p.props[idField], p.props.name].filter(Boolean).join(' ');
          return (
            <circle
              key={i}
              cx={p.lng * geo.k}
              cy={-p.lat}
              r={r}
              fill={colors?.get(value) || NEUTRAL}
              strokeWidth={r * 0.35}
              className={`bt-pointmap-dot ${dim ? 'is-dim' : ''}`}
            >
              <title>{[name || `#${i + 1}`, colorBy ? `${colorBy}：${formatCell(p.props[colorBy])}` : ''].filter(Boolean).join('，')}</title>
            </circle>
          );
        })}
        {showGroups &&
          groups!.map((g) => {
            const color = colors?.get(String(g.group_no)) || NEUTRAL;
            const dim = focus !== null && String(g.group_no) !== focus;
            const cx = g.centroid[0] * geo.k;
            const cy = -g.centroid[1];
            return (
              <g key={g.group_no} className={`bt-pointmap-centroid ${dim ? 'is-dim' : ''}`}>
                <circle cx={cx} cy={cy} r={r * 2.9} fill="none" stroke={color} strokeWidth={r * 0.55} />
                <text
                  x={cx}
                  y={cy}
                  fontSize={r * 3}
                  dy={r * 1.05}
                  textAnchor="middle"
                  fill={color}
                  stroke="var(--bt-bg-container)"
                  strokeWidth={r * 0.8}
                  paintOrder="stroke"
                >
                  {g.group_no}
                </text>
              </g>
            );
          })}
      </svg>
      {colors && (
        <figcaption className="bt-legend">
          {[...colors.entries()].map(([value, color]) => (
            <button
              key={value}
              type="button"
              className={`bt-legend-item ${focus === value ? 'is-active' : ''}`}
              aria-pressed={focus === value}
              onClick={() => setFocus(focus === value ? null : value)}
              style={{ '--c': color } as CSSProperties}
            >
              <i />
              {colorBy === 'group' ? `${value} 组` : value || '（空）'}
              <em>{counts.get(value) || 0}</em>
            </button>
          ))}
        </figcaption>
      )}
    </figure>
  );
}
