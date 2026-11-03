import { getTranslations, getLocale } from "next-intl/server";
export default async function NotFound() {
  const t = await getTranslations();
  const locale = await getLocale();
  return (
    <main id="main">
      <h1>{t("notFound")}</h1>
      <p>{t("notFoundHint")}</p>
      <a href={`/${locale}`}>{t("back")}</a>
    </main>
  );
}
