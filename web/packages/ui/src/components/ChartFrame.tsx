"use client";
import { useId, type ReactNode } from "react";
export interface ChartFrameProps {
  title: string;
  description: string;
  children?: ReactNode;
  dataLabel: string;
  data: ReactNode;
  status?: string;
}
export function ChartFrame({
  title,
  description,
  children,
  dataLabel,
  data,
  status,
}: ChartFrameProps) {
  const id = useId();
  return (
    <section
      className="sa-frame"
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
        <summary>{dataLabel}</summary>
        <div className="sa-data">{data}</div>
      </details>
    </section>
  );
}
