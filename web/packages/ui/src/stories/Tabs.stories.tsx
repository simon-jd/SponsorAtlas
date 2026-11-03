import { Tabs } from "../components/Tabs";
export default { title: "Components/Tabs" };
export const Keyboard = {
  render: () => (
    <Tabs
      label="Employer details"
      items={[
        {
          id: "overview",
          label: "Overview",
          content: <p>Public filing overview</p>,
        },
        {
          id: "disabled",
          label: "Unavailable",
          content: <p>Unavailable</p>,
          disabled: true,
        },
        { id: "wages", label: "Wages", content: <p>Reported annual wages</p> },
        {
          id: "sources",
          label: "Sources",
          content: <p>Public disclosure source</p>,
        },
      ]}
    />
  ),
};
export const Dark = { ...Keyboard, parameters: { theme: "dark" } };
export const Narrow = { ...Keyboard };
