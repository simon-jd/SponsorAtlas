import { cellToBoundary, cellToLatLng } from 'h3-js';
import type { Cell } from '../../lib/viz-data';
export function Fallback({
  cells,
  locale
}: {
  cells: Cell[];
  locale: string;
}) {
  const zh = locale === 'zh';
  return <div data-testid="viz-fallback">
<p role="status">{zh ? '图形加速不可用。以下表格提供相同聚合数据。' : 'Graphics acceleration unavailable. The table below contains the same aggregated evidence.'}</p>
<svg data-testid="viz-static-map" viewBox="0 0 720 360" role="img" aria-label={zh ? 'H3 聚合静态全球概览；完整数值在表格中' : 'Static global overview of H3 aggregates; full values follow in the table'} style={{
      width: '100%',
      background: '#122d40'
    }}>
{cells.flatMap(cell => {
        const center = cellToLatLng(cell.h3)[1];
        const points = cellToBoundary(cell.h3).map(([lat, lon]) => {
          let x = lon;
          while (x - center > 180) x -= 360;
          while (x - center < -180) x += 360;
          return [(x + 180) * 2, (90 - lat) * 2];
        });
        return [-720, 0, 720].map(offset => <polygon key={`${cell.h3}:${offset}`} points={points.map(([x, y]) => `${x + offset},${y}`).join(' ')} fill={`rgb(25,${120 + Math.min(90, Math.log2(cell.count + 1) * 9)},180)`} stroke="#eaf6fa" strokeWidth=".3" />);
      })}
</svg>
<CellTable cells={cells} locale={locale} />
</div>;
}
export function CellTable({
  cells,
  locale
}: {
  cells: Cell[];
  locale: string;
}) {
  const zh = locale === 'zh';
  const fmt = new Intl.NumberFormat(zh ? 'zh-CN' : 'en-US');
  return <div className="table-scroll" data-testid="viz-cell-table" tabIndex={0} role="region" aria-label={zh ? "H3 数据表" : "H3 data table"}>
<table>
<caption>{zh ? 'H3 单元聚合（全部单元）' : 'H3 aggregates (all cells)'}</caption>
<thead>
<tr>
<th scope="col">H3</th>
<th scope="col">{zh ? '申请数' : 'Applications'}</th>
<th scope="col">{zh ? '年薪中位数 (USD)' : 'Median annual wage (USD)'}</th>
</tr>
</thead>
<tbody>{cells.map(c => <tr key={c.h3}>
<th scope="row">{c.h3}</th>
<td>{fmt.format(c.count)}</td>
<td>{c.wage_p50 === null ? '—' : fmt.format(c.wage_p50)}</td>
</tr>)}</tbody>
</table>
</div>;
}
