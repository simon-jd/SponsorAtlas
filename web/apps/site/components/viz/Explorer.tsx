'use client';

import dynamic from 'next/dynamic';
import { useEffect, useState } from 'react';
import type { Aggregate } from '../../lib/viz-data';
const Map = dynamic(() => import('./WorksiteHexMap'), {
  ssr: false
});
const Wages = dynamic(() => import('./WageDistribution'), {
  ssr: false
});
export function Explorer({
  locale
}: {
  locale: string;
}) {
  const zh = locale === 'zh';
  const [enabled, setEnabled] = useState(false);
  const [resolution, setResolution] = useState('3');
  const [year, setYear] = useState('');
  const [employer, setEmployer] = useState('');
  const [scope, setScope] = useState({query: 'resolution=3'});
  const [data, setData] = useState<(Aggregate & {
    provenance: {
      kind: string;
      projection: string | null;
      version: string;
    };
  }) | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    fetch(`/api/viz?${scope.query}`, {
      signal: controller.signal
    }).then(r => {
      if (!r.ok) throw Error();
      return r.json();
    }).then(value => {
      setData(value);
      setLoading(false);
    }).catch(e => {
      if (e.name !== 'AbortError') setError(true);
    });
    return () => controller.abort();
  }, [scope]);
  return <>
<form onSubmit={e => {
      e.preventDefault();
      const p = new URLSearchParams({
        resolution
      });
      if (year) p.set('year', year);
      if (employer) p.set('employer', employer);
      setScope({query: p.toString()});
    }} className="viz-filters">
<label>{zh ? 'H3 分辨率' : 'H3 resolution'}<select value={resolution} onChange={e => setResolution(e.target.value)}>{[0, 1, 2, 3, 4, 5, 6, 7].map(r => <option key={r}>{r}</option>)}</select>
</label>
<label>{zh ? '财年' : 'Fiscal year'}<select value={year} onChange={e => setYear(e.target.value)}>
<option value="">{zh ? '全部已存档时期' : 'All archived periods'}</option>
<option>2024</option>
<option>2025</option>
</select>
</label>
<label>{zh ? '雇主标识（可选）' : 'Employer identifier (optional)'}<input value={employer} onChange={e => setEmployer(e.target.value)} maxLength={80} pattern="[A-Za-z0-9_-]*" />
</label>
<button>{zh ? '应用范围' : 'Apply scope'}</button>
</form>
 {error ? <p role="alert">{zh ? '可视化数据暂不可用，请检查范围或稍后重试。' : 'Visualization unavailable. Check the scope or retry later.'}</p> : !data ? <p role="status">{zh ? '加载聚合数据…' : 'Loading aggregates…'}</p> : <>
 <div data-testid="viz-coverage" aria-busy={loading}>
<p>{zh ? '所选申请' : 'Selected applications'}: {data.coverage.total.toLocaleString(locale)} · {zh ? '已映射' : 'Mapped'}: {data.coverage.mapped.toLocaleString(locale)} · {zh ? '未映射' : 'Unmapped'}: {data.coverage.unmapped.toLocaleString(locale)} · {zh ? '缺失工资' : 'Missing wage'}: {data.coverage.nullWages.toLocaleString(locale)}</p>
<p className="identifier">{data.provenance.projection ? zh ? '投影 SHA-256' : 'Projection SHA-256' : zh ? '实时来源版本' : 'Live source revision'}: {data.provenance.projection ?? data.provenance.version}</p>
<details>
<summary>{zh ? '来源年份与历史状态' : 'Source vintages and history status'}</summary>{[data.coverage.vintages, data.coverage.mapping, data.coverage.history].map((group, i) => <ul key={i}>{Object.entries(group).map(([k, n]) => <li key={k}>{k}: {n.toLocaleString(locale)}</li>)}</ul>)}</details>
</div>
 {!data.coverage.total ? <p role="status">{zh ? '此范围无数据。' : 'No data in this scope.'}</p> : !enabled ? <button data-testid="enable-map" onClick={() => setEnabled(true)}>{zh ? '加载交互地图和工资分布' : 'Load interactive map and wage distribution'}</button> : <>{data.cells.length ? <Map cells={data.cells} locale={locale} resolution={data.resolution} onResolutionChange={r => {
          setResolution(String(r));
          const next = new URLSearchParams(scope.query);
          next.set("resolution", String(r));
          setScope({query: next.toString()});
        }} /> : <p role="status">{zh ? '此范围无有效坐标。' : 'No valid coordinates in this scope.'}</p>}<Wages bins={data.histogram} locale={locale} />
</>}
 </>}
 </>;
}
