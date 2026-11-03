import { notFound } from "next/navigation";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { employerDetails } from "../../../../lib/cube";
import { parseIds, validId, type EmployerRow } from "../../../../lib/data";
import { requireLocale, single, type SearchParams } from "../../../../lib/page";
import { EvidenceTable } from "../../../../components/EvidenceTable";
import { Context } from "../../../../components/Context";
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string; id: string }>;
  searchParams: SearchParams;
}) {
  const p = await params;
  const locale = requireLocale(p.locale);
  setRequestLocale(locale);
  const t = await getTranslations();
  if (!validId(p.id)) notFound();
  let ids: string[];
  try {
    ids = parseIds(single((await searchParams).ids));
  } catch {
    notFound();
  }
  let rows: EmployerRow[] = [];
  let unavailable = false;
  try {
    rows = await employerDetails(p.id);
  } catch {
    unavailable = true;
  }
  if (!unavailable && !rows.length) notFound();
  const labels = Object.fromEntries(
    [
      "employer",
      "year",
      "history",
      "historyMissing",
      "applications",
      "wage",
      "caption",
      "empty",
    ].map((key) => [key, t(key)]),
  );
  const added = ids.includes(p.id);
  const full = ids.length >= 3 && !added;
  const suffix = ids.length ? "?ids=" + encodeURIComponent(ids.join(",")) : "";
  return (
    <main id="main">
      <a href={`/${locale}${suffix}`}>
        {t("back")}
      </a>
      <section className="hero compact">
        <p className="eyebrow">{t("details")}</p>
        <h1>{t("employer")}</h1>
        <p className="identifier detail-id">{p.id}</p>
        {!unavailable &&
          (full ? (
            <p role="status">{t("full")}</p>
          ) : (
            <a
              className="action"
              data-add-compare
              href={`/${locale}/compare?ids=${encodeURIComponent([...new Set([...ids, p.id])].join(","))}`}
            >
              {t(added ? "selected" : "add")}
            </a>
          ))}
      </section>
      <h2>{t("period")}</h2>
      <p>{t("compareHint")}</p>
      {unavailable ? (
        <p role="alert">
          {t("unavailable")}{" "}
          <a href={`/${locale}/employers/${p.id}${suffix}`}>{t("retry")}</a>
        </p>
      ) : (
        <EvidenceTable rows={rows} locale={locale} labels={labels} />
      )}
      <Context />
    </main>
  );
}
