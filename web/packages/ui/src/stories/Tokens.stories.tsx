import "../generated/preview-utilities.css";
export default { title: "Foundations/Tokens" };
export const Light = {
  render: () => (
    <div
      data-testid="token-probe"
      className="bg-surface text-ink p-4 text-lg rounded-md duration-fast"
    >
      Generated semantic token integration
    </div>
  ),
};
export const Dark = { ...Light, parameters: { theme: "dark" } };
