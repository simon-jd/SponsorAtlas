"use client";
import {
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
export interface TabItem {
  id: string;
  label: string;
  content: ReactNode;
  disabled?: boolean;
}
export interface TabsProps {
  label: string;
  items: readonly TabItem[];
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
}
/** Horizontal automatic-activation tabs. Panels should contain already available content. */
export function Tabs({
  label,
  items,
  value,
  defaultValue,
  onValueChange,
}: TabsProps) {
  const id = useId();
  const [internal, setInternal] = useState(defaultValue);
  const refs = useRef(new Map<string, HTMLButtonElement>());
  const enabled = items.filter((item) => !item.disabled);
  const requested = value ?? internal;
  const selected =
    enabled.find((item) => item.id === requested)?.id ?? enabled[0]?.id;
  const select = (next: string) => {
    if (value === undefined) setInternal(next);
    onValueChange?.(next);
  };
  const navigate = (
    event: KeyboardEvent<HTMLButtonElement>,
    current: string,
  ) => {
    const index = enabled.findIndex((item) => item.id === current);
    const next =
      event.key === "ArrowRight"
        ? enabled[(index + 1) % enabled.length]
        : event.key === "ArrowLeft"
          ? enabled[(index - 1 + enabled.length) % enabled.length]
          : event.key === "Home"
            ? enabled[0]
            : event.key === "End"
              ? enabled.at(-1)
              : undefined;
    if (next) {
      event.preventDefault();
      select(next.id);
      refs.current.get(next.id)?.focus();
    }
  };
  return (
    <div className="sa-tabs">
      <div role="tablist" aria-label={label} aria-orientation="horizontal">
        {items.map((item, index) => (
          <button
            key={item.id}
            id={`${id}-tab-${index}`}
            type="button"
            role="tab"
            aria-selected={selected === item.id}
            aria-controls={`${id}-panel-${index}`}
            disabled={item.disabled}
            tabIndex={selected === item.id ? 0 : -1}
            ref={(element) => {
              if (element) refs.current.set(item.id, element);
              else refs.current.delete(item.id);
            }}
            onClick={() => select(item.id)}
            onKeyDown={(event) => navigate(event, item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      {items.map((item, index) => (
        <div
          key={item.id}
          id={`${id}-panel-${index}`}
          role="tabpanel"
          aria-labelledby={`${id}-tab-${index}`}
          hidden={selected !== item.id}
          tabIndex={0}
        >
          {item.content}
        </div>
      ))}
    </div>
  );
}
