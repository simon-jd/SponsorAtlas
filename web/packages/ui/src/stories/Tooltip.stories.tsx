import { Tooltip } from "../components/Tooltip";
export default { title: "Components/Tooltip" };
export const Keyboard = {
  render: () => (
    <>
      <Tooltip
        label="About filings"
        content="LCA filings are not visa approvals."
      />
      <button>Next control</button>
    </>
  ),
};
export const Dark = { ...Keyboard, parameters: { theme: "dark" } };
