import { useState } from "react";
import { Combobox } from "../components/Combobox";
const options = [
  { value: "atlas", label: "Atlas Research" },
  { value: "cedar", label: "Cedar Labs" },
  { value: "closed", label: "Closed employer", disabled: true },
];
function Example({ empty = false, error = false, disabled = false }) {
  const [value, setValue] = useState("");
  return (
    <>
      <Combobox
        label="Employer"
        options={empty ? [] : options}
        value={value}
        onValueChange={setValue}
        emptyMessage="No employers found"
        error={error ? "Employer data unavailable" : undefined}
        disabled={disabled}
      />
      <output aria-label="Chosen employer">{value || "None"}</output>
      <button>Next control</button>
    </>
  );
}
export default { title: "Components/Combobox" };
export const Keyboard = { render: () => <Example /> };
export const Empty = { render: () => <Example empty /> };
export const Error = { render: () => <Example error /> };
export const Disabled = { render: () => <Example disabled /> };
export const Dark = { ...Keyboard, parameters: { theme: "dark" } };
