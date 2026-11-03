"use client";
import { useEffect, useId, useState, type KeyboardEvent } from "react";
export interface ComboboxOption {
  value: string;
  label: string;
  disabled?: boolean;
}
export interface ComboboxProps {
  label: string;
  options: readonly ComboboxOption[];
  value: string;
  onValueChange: (value: string) => void;
  emptyMessage: string;
  placeholder?: string;
  error?: string;
  disabled?: boolean;
}
/** Editable, single-select list autocomplete. Focus always stays on the input. */
export function Combobox({
  label,
  options,
  value,
  onValueChange,
  emptyMessage,
  placeholder,
  error,
  disabled,
}: ComboboxProps) {
  const id = useId();
  const selected = options.find((option) => option.value === value);
  const [query, setQuery] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState<string | null>(null);
  const filtered = options.filter(
    (option) =>
      query === null ||
      option.label.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
  const enabled = filtered.filter((option) => !option.disabled);
  const activeOption = enabled.find((option) => option.value === active);
  const optionId = (option: ComboboxOption) =>
    `${id}-option-${options.indexOf(option)}`;
  const activeId = activeOption ? optionId(activeOption) : undefined;
  useEffect(() => {
    if (open && activeId)
      document.getElementById(activeId)?.scrollIntoView({ block: "nearest" });
  }, [open, activeId]);
  const close = () => {
    setOpen(false);
    setActive(null);
    setQuery(null);
  };
  const choose = (option: ComboboxOption) => {
    onValueChange(option.value);
    close();
  };
  const keyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      if (open) {
        event.preventDefault();
        close();
      }
      return;
    }
    if (event.key === "Enter" && open) {
      event.preventDefault();
      if (activeOption) choose(activeOption);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      const index = enabled.findIndex((option) => option.value === active);
      const next =
        !open || index < 0
          ? event.key === "ArrowDown"
            ? 0
            : enabled.length - 1
          : event.key === "ArrowDown"
            ? (index + 1) % enabled.length
            : (index - 1 + enabled.length) % enabled.length;
      setActive(enabled[next]?.value ?? null);
    }
    if (open && (event.key === "Home" || event.key === "End")) {
      event.preventDefault();
      setActive(
        enabled[event.key === "Home" ? 0 : enabled.length - 1]?.value ?? null,
      );
    }
  };
  return (
    <div className="sa-combobox">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        role="combobox"
        autoComplete="off"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={open ? `${id}-list` : undefined}
        aria-activedescendant={
          open && activeOption ? optionId(activeOption) : undefined
        }
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        value={query ?? selected?.label ?? ""}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
          setActive(null);
          if (value) onValueChange("");
        }}
        onKeyDown={keyDown}
        onBlur={close}
      />
      {error && (
        <p className="sa-error" id={`${id}-error`}>
          {error}
        </p>
      )}
      {open && (
        <div className="sa-popup">
          <ul id={`${id}-list`} role="listbox" aria-label={label}>
            {filtered.map((option) => (
              <li
                id={optionId(option)}
                key={option.value}
                role="option"
                aria-selected={option.value === value}
                aria-disabled={option.disabled || undefined}
                data-active={option.value === active}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => {
                  if (!option.disabled) choose(option);
                }}
              >
                {option.label}
              </li>
            ))}
          </ul>
          {!filtered.length && <p role="status">{emptyMessage}</p>}
        </div>
      )}
    </div>
  );
}
