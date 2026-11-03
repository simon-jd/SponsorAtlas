import { getTranslations } from "next-intl/server";
import { dataIdentity } from "../lib/cube-settings";
export async function Context() {
  const t = await getTranslations();
  let identity: ReturnType<typeof dataIdentity> | undefined;
  try {
    identity = dataIdentity(process.env);
  } catch {
    /* Do not claim invalid provenance. */
  }
  const source = identity?.source;
  const digest = source === "parquet" ? identity?.version : undefined;
  return (
    <aside className="context" aria-labelledby="context-title">
      <h2 id="context-title">{t("limitsTitle")}</h2>
      <p className="disclaimer">{t("disclaimer")}</p>
      <p>{t("coverage")}</p>
      <p>{t("identity")}</p>
      <p>{t("missing")}</p>
      <details>
        <summary>{t("source")}</summary>
        <p>
          {t(
            source === "fixture"
              ? "fixture"
              : source === "parquet"
                ? "parquet"
                : source === "snowflake"
                  ? "snowflake"
                  : "notConfigured",
          )}
        </p>
        <p>
          <a href="https://www.dol.gov/agencies/eta/foreign-labor/performance">
            {t("archive")}
          </a>{" "}
          · MARTS.fact_lca_case → lca_metrics
        </p>
        {source === "parquet" && digest && /^[a-f0-9]{64}$/.test(digest) && (
          <p>
            {t("snapshot")}: <code className="identifier">{digest}</code>
          </p>
        )}
        <p>{t("freshness")}</p>
      </details>
    </aside>
  );
}
