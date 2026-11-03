'use client';

import DeckGL from '@deck.gl/react';
import { PolygonLayer } from '@deck.gl/layers';
import { OrthographicView, COORDINATE_SYSTEM } from '@deck.gl/core';
import { useEffect, useState } from 'react';
type Bin = {
  lower: number;
  upper: number | null;
  count: number;
};
export default function WageDistribution({
  bins,
  locale
}: {
  bins: Bin[];
  locale: string;
}) {
  const zh = locale === 'zh';
  const [gpu, setGpu] = useState(false);
  useEffect(() => {
    const gl = document.createElement('canvas').getContext('webgl2');
    setGpu(Boolean(gl));
    gl?.getExtension('WEBGL_lose_context')?.loseContext();
  }, []);
  const max = Math.max(1, ...bins.map(b => b.count));
  const bars = bins.map((b, i) => ({
    ...b,
    polygon: [[i * 40, 0], [i * 40 + 30, 0], [i * 40 + 30, b.count / max * 120], [i * 40, b.count / max * 120]]
  }));
  return <section className="wage-distribution">
<h2>{zh ? '年薪分布 (USD)' : 'Annual wage distribution (USD)'}</h2>
<p>{zh ? '所有所选申请（包括未映射地点）；缺失工资不进入分布。' : 'All selected applications, including unmapped locations; missing wages are excluded.'}</p>{gpu && <div className="wage-chart" aria-hidden="true">
<DeckGL views={new OrthographicView({
        id: 'wages',
        flipY: false
      })} initialViewState={{
        target: [215, 60, 0],
        zoom: 0
      }} controller={false} useDevicePixels={1} onError={() => setGpu(false)} layers={[new PolygonLayer({
        id: 'wage-bins',
        data: bars,
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        getPolygon: d => d.polygon,
        getFillColor: [24, 142, 175],
        stroked: false
      })]} />
</div>}{!gpu&&<svg data-testid="wage-static-chart" viewBox="0 0 440 160" role="img" aria-label={zh?'静态工资分布；下表提供完整数值':'Static wage distribution; full values follow in the table'} className="wage-chart">{bins.map((bin,i)=><rect key={bin.lower} x={i*40} y={140-bin.count/max*120} width={30} height={bin.count/max*120} fill="#188eaf"/>)}</svg>}<table>
<caption>{zh ? '工资分组与申请数' : 'Wage bands and application counts'}</caption>
<thead>
<tr>
<th scope="col">USD</th>
<th scope="col">{zh ? '申请数' : 'Applications'}</th>
</tr>
</thead>
<tbody>{bins.map(b => <tr key={b.lower}>
<th scope="row">{b.lower.toLocaleString(locale)}–{b.upper === null ? '+' : `<${b.upper.toLocaleString(locale)}`}</th>
<td>{b.count.toLocaleString(locale)}</td>
</tr>)}</tbody>
</table>
</section>;
}
