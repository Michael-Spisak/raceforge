import { defineConfig } from "@playwright/test";
import { delimiter, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = fileURLToPath(new URL("..", import.meta.url)); // package is ESM: no __dirname

const port = 8799;

export default defineConfig({
  testDir: "e2e",
  timeout: 240_000,
  use: { baseURL: `http://127.0.0.1:${port}`, viewport: { width: 1280, height: 800 } },
  webServer: {
    // The real engine serves the built UI (npm run build first). cwd/env instead of shell syntax so the
    // command also works with cmd.exe on Windows.
    command: `uv run raceforge ui --port ${port}`,
    cwd: repoRoot,
    env: { PYTHONPATH: [resolve(repoRoot, "src"), process.env.PYTHONPATH].filter(Boolean).join(delimiter) },
    url: `http://127.0.0.1:${port}/api/v1/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
