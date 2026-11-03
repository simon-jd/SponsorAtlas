import { getTranslations } from "next-intl/server";
export default async function Loading() {
  const t = await getTranslations();
  return (
    <main id="main" role="status">
      {t("loading")}
    </main>
  );
}
