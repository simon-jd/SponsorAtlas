import { Vitals } from "../../components/Vitals";
import { Suspense } from "react";
import { cookies } from "next/headers";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { requireLocale } from "../../lib/page";
import { Navigation } from "../../components/Navigation";
import "ui/styles.css";
import "../site.css";
export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const locale = requireLocale((await params).locale);
  const t = await getTranslations({ locale });
  return { title: t("metaTitle"), description: t("metaDescription") };
}
export default async function Layout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const locale = requireLocale((await params).locale);
  setRequestLocale(locale);
  const t = await getTranslations();
  const theme =
    (await cookies()).get("sa-theme")?.value === "dark" ? "dark" : "light";
  const labels = Object.fromEntries(
    ["nav", "home", "compare", "language", "theme", "themeLight"].map((key) => [
      key,
      t(key),
    ]),
  );
  return (
    <html lang={locale} data-theme={theme}>
      <body>
        {process.env.RUM_ENABLED === "1" && <Vitals />}
        <a className="skip" href="#main">
          {t("skip")}
        </a>
        <header>
          <a className="brand" href={`/${locale}`} aria-label="S/A SponsorAtlas">
            <span aria-hidden="true">S/A</span> <span>SponsorAtlas</span>
          </a>
          <Suspense>
            <Navigation locale={locale} initialTheme={theme} labels={labels} />
          </Suspense>
        </header>
        {process.env.SITE_DATA_SOURCE === "fixture" && (
          <div className="test-banner">{t("fixture")}</div>
        )}
        {children}
        <footer>SponsorAtlas · {t("disclaimer")}</footer>
      </body>
    </html>
  );
}
