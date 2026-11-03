import StyleDictionary from "style-dictionary";
import { readFile, writeFile, mkdir } from "node:fs/promises";
const source = JSON.parse(
  await readFile(
    new URL("../../../design/tokens.json", import.meta.url),
    "utf8",
  ),
);
const output = new URL("./src/generated/", import.meta.url);
await mkdir(output, { recursive: true });
const { color, ...shared } = source;
// Each theme uses the same semantic variable names, so consumers never switch classes.
for (const mode of ["light", "dark"]) {
  const dictionary = new StyleDictionary({
    tokens: { ...shared, color: color[mode] },
    platforms: {
      css: {
        transformGroup: "css",
        prefix: "sa",
        buildPath: output.pathname,
        files: [
          {
            destination: `${mode}.css`,
            format: "css/variables",
            options: {
              showFileHeader: false,
              selector:
                mode === "light"
                  ? ':root, [data-theme="light"]'
                  : '[data-theme="dark"]',
            },
          },
        ],
      },
    },
  });
  await dictionary.buildAllPlatforms();
}
await writeFile(
  new URL("tokens.css", output),
  (await readFile(new URL("light.css", output), "utf8")) +
    "\n" +
    (await readFile(new URL("dark.css", output), "utf8")),
);
const groups = [
  ["color", color.light, "color"],
  ["spacing", source.space, "space"],
  ["text", source.font.size, "font-size"],
  ["font", source.font.family, "font-family"],
  ["radius", source.radius, "radius"],
  ["transition-duration", source.motion, "motion"],
];
const kebab = (name) =>
  name.replace(/[A-Z]/g, (letter) => "-" + letter.toLowerCase());
const aliases = groups.flatMap(([namespace, values, prefix]) =>
  Object.keys(values).map(
    (key) =>
      `  --${namespace}-${kebab(key)}: var(--sa-${prefix}-${kebab(key)});`,
  ),
);
await writeFile(
  new URL("tailwind.css", output),
  `/* Tailwind v4 theme; import after tailwindcss and ui/tokens.css. */\n@theme inline {\n${aliases.join("\n")}\n}\n`,
);
// Compile a tiny preview fixture so Storybook checks computed Tailwind styles in Chrome.
const { compile } = await import("tailwindcss");
const theme = await readFile(new URL("tailwind.css", output), "utf8");
const utilities = await compile(theme + "\n@tailwind utilities;");
await writeFile(
  new URL("preview-utilities.css", output),
  utilities.build([
    "bg-surface",
    "text-ink",
    "p-4",
    "text-lg",
    "rounded-md",
    "duration-fast",
  ]),
);
