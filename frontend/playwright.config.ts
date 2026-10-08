import { defineConfig } from "@playwright/test";

const port = 8799;

export default defineConfig({
  testDir: "e2e",
  timeout: 240_000,
  use: { baseURL: `http://127.0.0.1:${port}`, viewport: { width: 1280, height: 800 } },
  webServer: {
    // The real engine serves the built UI (npm run build first).
    command: `cd .. && PYTHONPATH=src uv run raceforge ui --port ${port}`,
    url: `http://127.0.0.1:${port}/api/v1/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
