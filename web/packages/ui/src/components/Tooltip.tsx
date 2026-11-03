"use client";
import { useEffect, useId, useState } from "react";
export interface TooltipProps {
  label: string;
  content: string;
}
/** Supplemental text only; important instructions must also appear in persistent content. */
export function Tooltip({ label, content }: TooltipProps) {
  const id = useId();
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const open = (hovered || focused) && !dismissed;
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDismissed(true);
    };
    document.addEventListener("keydown", dismiss);
    return () => document.removeEventListener("keydown", dismiss);
  }, [open]);
  return (
    <span
      className="sa-tooltip"
      onMouseEnter={() => {
        setHovered(true);
        setDismissed(false);
      }}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        type="button"
        className="sa-tooltip-trigger"
        aria-describedby={open ? id : undefined}
        onFocus={() => {
          setFocused(true);
          setDismissed(false);
        }}
        onBlur={() => setFocused(false)}
      >
        {label}
      </button>
      {open && (
        <span className="sa-tooltip-content" id={id} role="tooltip">
          {content}
        </span>
      )}
    </span>
  );
}
