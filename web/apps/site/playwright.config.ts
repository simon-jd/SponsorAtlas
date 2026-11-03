import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests/e2e",
  workers: 1,
  fullyParallel: false,
  timeout: 35000,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3100",
    viewport: { width: 1280, height: 900 },
    launchOptions: {
      executablePath: "/usr/bin/google-chrome",
      args: ["--disable-gpu"],
    },
  },
});
