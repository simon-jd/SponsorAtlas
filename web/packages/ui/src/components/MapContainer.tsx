"use client";
import { useId, type ReactNode } from "react";
export interface MapContainerProps {
  title: string;
  description: string;
  children?: ReactNode;
  fallbackLabel: string;
  fallback: ReactNode;
  status?: string;
}
/** A named map slot and always reachable non-graphical alternative; no WebGL dependency. */
export function MapContainer({
  title,
  description,
  children,
  fallbackLabel,
  fallback,
  status,
}: MapContainerProps) {
  const id = useId();
  return (
    <section
      className="sa-frame sa-map"
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-description`}
    >
      <h2 id={`${id}-title`}>{title}</h2>
      <p id={`${id}-description`}>{description}</p>
      {status ? (
        <p role="status" className="sa-unavailable">
          {status}
        </p>
      ) : (
        <div className="sa-graphic">{children}</div>
      )}
      <details>
        <summary>{fallbackLabel}</summary>
        <div className="sa-data">{fallback}</div>
      </details>
    </section>
  );
}
