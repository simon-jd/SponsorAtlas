import { setRequestLocale } from 'next-intl/server';
import { requireLocale } from '../../../lib/page';
import { Explorer } from '../../../components/viz/Explorer';
import { Context } from '../../../components/Context';
export default async function Page({
  params
}: {
  params: Promise<{
    locale: string;
  }>;
}) {
  const locale = requireLocale((await params).locale);
  setRequestLocale(locale);
  const zh = locale === 'zh';
  return <main id="main">
<section className="hero compact">
<h1>{zh ? '申请地点与工资' : 'Application geography and wages'}</h1>
<p>{zh ? '位置取工作地邮编区（ZCTA）的 Census2020 内部参考点，其次是工作地城市的参考点，最后才用县内部参考点；都不是精确工作地址。未匹配记录保留在覆盖统计中。' : 'Locations are Census2020 internal points of the worksite ZIP area (ZCTA), else of the worksite city, else of the county; none is a precise worksite address. Unmatched records remain in coverage counts.'}</p>
<p>{zh ? '缩放层级改变时，服务端重新计算 H3 聚合；也可手动选择分辨率。每个单元的工资中位数直接根据所选原始工资频数计算，不是县中位数的中位数。' : 'Changing zoom levels requests new server H3 aggregation; resolution can also be selected manually. Cell medians use the selected underlying wage frequencies, not medians of county medians.'}</p>
</section>
<Explorer locale={locale} />
<Context />
</main>;
}
