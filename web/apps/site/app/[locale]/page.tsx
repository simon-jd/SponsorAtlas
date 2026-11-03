import { getTranslations, setRequestLocale } from "next-intl/server";
import { Button } from "ui";
import { searchEmployers, unresolvedCount } from "../../lib/cube";
import { parseIds, validId, type EmployerRow } from "../../lib/data";
import { formatNumber } from "../../lib/format";
import { requireLocale, single, type SearchParams } from "../../lib/page";
import { Context } from "../../components/Context";
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: SearchParams;
}) {
  const locale = requireLocale((await params).locale);
  setRequestLocale(locale);
  const t = await getTranslations();
  const search = await searchParams;
  const q = single(search.q).trim();
  let ids: string[] = [];
  let invalid = false;
  try {
    ids = parseIds(single(search.ids));
  } catch {
    invalid = true;
  }
  if (q && !validId(q)) invalid = true;
  let rows: EmployerRow[] = [];
  let unavailable = false;
  let unresolved: number | null = null;
  if (!invalid)
    try {
      rows = await searchEmployers(q);
      unresolved = await unresolvedCount();
    } catch {
      unavailable = true;
    }
  const suffix = ids.length ? "?ids=" + encodeURIComponent(ids.join(",")) : "";
  const retrySearch = new URLSearchParams({ q });
  if (ids.length) retrySearch.set("ids", ids.join(","));
  return (
    <main id="main">
      <section className="hero">
        <p className="eyebrow">{t("eyebrow")}</p>
        <h1>{t("title")}</h1>
        <p className="intro">{t("intro")}</p>
        <form action={`/${locale}`} className="search">
          <label htmlFor="employer-query">{t("searchLabel")}</label>
          <p id="search-hint">{t("searchHint")}</p>
          <div className="search-row">
            <input
              id="employer-query"
              name="q"
              maxLength={80}
              defaultValue={q}
              aria-describedby="search-hint"
              autoComplete="off"
              spellCheck={false}
            />
            {ids.length > 0 && (
              <input type="hidden" name="ids" value={ids.join(",")} />
            )}
            <Button type="submit">{t("search")}</Button>
          </div>
        </form>
      </section>
      <section aria-labelledby="results-title">
        <div className="section-heading">
          <h2 id="results-title">{t("results")}</h2>
          <span className="tag">LCA · Q4</span>
        </div>
        <p>{t("resultHint")}</p>
        {unresolved !== null && (
          <p className="quality">
            {t("unresolved", { count: formatNumber(unresolved, locale) })}
          </p>
        )}
        {invalid ? (
          <p role="alert">{t("inputError")}</p>
        ) : unavailable ? (
          <p role="alert">
            {t("unavailable")}{" "}
            <a href={`/${locale}?${retrySearch}`}>{t("retry")}</a>
          </p>
        ) : rows.length === 0 ? (
          <p role="status">{t("empty")}</p>
        ) : (
          <ul className="results">
            {rows.slice(0, 12).map((row) => (
              <li key={row.id}>
                <a
                  data-employer-link={row.id}
                  href={`/${locale}/employers/${row.id}${suffix}`}
                >
                  <span className="small-label">{t("employer")}</span>
                  <span className="identifier">{row.id}</span>
                  <span className="count">
                    <strong>{formatNumber(row.count, locale)}</strong>{" "}
                    {t("applications")} <span aria-hidden="true">↗</span>
                  </span>
                </a>
              </li>
            ))}
          </ul>
        )}
        {rows.length > 12 && <p role="status">{t("more")}</p>}
      </section>
      <Context />
    </main>
  );
}
