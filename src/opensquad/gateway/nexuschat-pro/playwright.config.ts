import { defineConfig, devices } from '@playwright/test';

/**
 * Critical-path UI smoke against a running Gateway (default :9555).
 * Start the stack first: `uv run opensquad start`
 *
 * Env:
 *   E2E_BASE_URL   default http://127.0.0.1:9555
 *   E2E_EMAIL      default ss@ss (local smoke account)
 *   E2E_PASSWORD   default ssssss
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: 'list',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:9555',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
