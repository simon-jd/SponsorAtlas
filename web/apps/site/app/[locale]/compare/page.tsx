import { getTranslations, setRequestLocale } from "next-intl/server";
import { employerDetails } from "../../../lib/cube";
import { parseIds, type EmployerRow } from "../../../lib/data";
import { requireLocale, single, type SearchParams } from "../../../lib/page";
import { EvidenceTable } from "../../../components/EvidenceTable";
import { Context } from "../../../components/Context";
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
  let ids: string[] = [];
  let invalid = false;
  try {
    ids = parseIds(single((await searchParams).ids));
  } catch {
    invalid = true;
  }
  let rows: EmployerRow[] = [];
  let unavailable = false;
  if (!invalid)
    try {
      for (const id of ids) rows.push(...(await employerDetails(id)));
    } catch {
      unavailable = true;
    }
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
  return (
    <main id="main">
      <section className="hero compact">
        <p className="eyebrow">LCA · Q4</p>
        <h1>{t("compareTitle")}</h1>
        <p className="intro">{t("compareHint")}</p>
        <a
          href={`/${locale}${ids.length ? "?ids=" + encodeURIComponent(ids.join(",")) : ""}`}
        >
          {t("back")}
        </a>
      </section>
      {invalid ? (
        <p role="alert">{t("inputError")}</p>
      ) : unavailable ? (
        <p role="alert">{t("unavailable")}</p>
      ) : !ids.length ? (
        <p role="status">{t("choose")}</p>
      ) : (
        <>
          <ul className="selections">
            {ids.map((id) => (
              <li key={id}>
                <span className="identifier">{id}</span>
                <a
                  aria-label={`${t("remove")} ${id}`}
                  href={`/${locale}/compare?ids=${encodeURIComponent(ids.filter((value) => value !== id).join(","))}`}
                >
                  {t("remove")}
                </a>
              </li>
            ))}
          </ul>
          <EvidenceTable rows={rows} locale={locale} labels={labels} />
        </>
      )}
      <Context />
    </main>
  );
}
