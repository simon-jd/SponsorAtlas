import type { Preview } from "@storybook/react-vite";
import "../src/styles.css";
const preview: Preview = {
  parameters: { a11y: { test: "error" }, layout: "padded" },
  decorators: [
    (Story, context) => (
      <main
        className="sa-preview"
        lang="en"
        data-theme={context.parameters.theme ?? "light"}
      >
        <Story />
      </main>
    ),
  ],
};
export default preview;
