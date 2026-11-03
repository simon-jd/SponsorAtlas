import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: 'tests/e2e',
  testMatch: 'viz.spec.ts',
  workers: 1,
  timeout: 45000,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:3100',
    viewport: {
      width: 1280,
      height: 900
    },
    launchOptions: {
      executablePath: '/usr/bin/google-chrome',
      args: ['--no-sandbox', '--enable-webgl', '--ignore-gpu-blocklist', ...(process.env.VIZ_GPU === 'vulkan' ? ['--use-angle=vulkan', '--enable-features=Vulkan', '--disable-vulkan-surface'] : [])]
    }
  }
});
