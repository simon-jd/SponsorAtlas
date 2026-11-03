"use client";
import { useState } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { Button } from "ui";
export function Navigation({
  locale,
  initialTheme,
  labels,
}: {
  locale: string;
  initialTheme: "light" | "dark";
  labels: Record<string, string>;
}) {
  const pathname = usePathname();
  const params = useSearchParams();
  const [theme, setTheme] = useState(initialTheme);
  const other = locale === "en" ? "zh" : "en";
  const query = params.toString();
  const ids = params.get("ids");
  return (
    <nav aria-label={labels.nav}>
      <a href={`/${locale}${ids ? "?ids=" + encodeURIComponent(ids) : ""}`}>
        {labels.home}
      </a>
      <a
        href={`/${locale}/compare${ids ? "?ids=" + encodeURIComponent(ids) : ""}`}
      >
        {labels.compare}
      </a>
      <a href={`/${locale}/map`}>{locale === "zh" ? "地点与工资" : "Geography & wages"}</a>
      <a
        data-language
        href={`/${other}${pathname.slice(3)}${query ? "?" + query : ""}`}
        lang={other}
        hrefLang={other}
      >
        {labels.language}
      </a>
      <Button
        data-theme-toggle
        variant="secondary"
        onClick={() => {
          const next = theme === "light" ? "dark" : "light";
          document.documentElement.dataset.theme = next;
          document.cookie = `sa-theme=${next}; Path=/; Max-Age=31536000; SameSite=Lax`;
          setTheme(next);
        }}
      >
        {theme === "light" ? labels.theme : labels.themeLight}
      </Button>
    </nav>
  );
}
