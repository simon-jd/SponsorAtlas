import { notFound } from "next/navigation";
import type { Locale } from "./format";
export function requireLocale(value: string): Locale {
  if (value !== "en" && value !== "zh") notFound();
  return value;
}
export function single(value: string | string[] | undefined) {
  return typeof value === "string" ? value : "";
}
export type SearchParams = Promise<
  Record<string, string | string[] | undefined>
>;
