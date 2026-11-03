export type Locale = "en" | "zh";
const tag = (locale: Locale) => (locale === "zh" ? "zh-CN" : "en-US");
const missing = (locale: Locale) =>
  locale === "zh" ? "暂无数据" : "Not available";
function finite(n: number) {
  if (!Number.isFinite(n)) throw Error("Expected finite number");
  return n;
}
export function formatCurrency(n: number | null, locale: Locale) {
  return n === null
    ? missing(locale)
    : new Intl.NumberFormat(tag(locale), {
        style: "currency",
        currency: "USD",
      }).format(finite(n));
}
export function formatNumber(n: number | null, locale: Locale) {
  return n === null
    ? missing(locale)
    : new Intl.NumberFormat(tag(locale)).format(finite(n));
}
export function formatDate(d: string | null, locale: Locale) {
  if (d === null) return missing(locale);
  const date = new Date(`${d}T00:00:00Z`);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(d) ||
    !Number.isFinite(date.valueOf()) ||
    date.toISOString().slice(0, 10) !== d
  )
    throw Error("Expected valid date-only ISO date");
  return new Intl.DateTimeFormat(tag(locale), {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(date);
}
