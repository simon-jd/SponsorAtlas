const { getJestConfig } = require("@storybook/test-runner");
const base = getJestConfig();
module.exports = {
  ...base,
  testMatch: process.env.UI_TEST_STORY
    ? [`**/${process.env.UI_TEST_STORY}.stories.tsx`]
    : base.testMatch,
  setupFilesAfterEnv: [
    ...base.setupFilesAfterEnv,
    require("node:path").resolve(__dirname, "tests/setup-runner.mjs"),
  ],
  cacheDirectory: require("node:path").resolve(__dirname, "../../.cache/jest"),
  testEnvironmentOptions: {
    "jest-playwright": {
      browsers: ["chromium"],
      launchOptions: {
        executablePath: "/usr/bin/google-chrome",
        headless: true,
      },
      contextOptions: { reducedMotion: "reduce" },
    },
  },
};
