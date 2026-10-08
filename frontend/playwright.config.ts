import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = fileURLToPath(new URL("..", import.meta.url)); // package is ESM: no __dirname

const port = 8799;
export const fakeCarPort = 8792;
export const FAKE_CAR_TOKEN = "e2e-fake-car-token-123";
const backendPort = 8798;
// Spec 0006 AC10: a backend for the Team tab. RF_E2E_BACKEND_URL points at a running stack (CI: the
// Compose stack); otherwise a local SQLite dev backend is started. Its admin has TOTP preset.
export const E2E_ADMIN = { username: "admin", password: "e2e-admin-password", totp: "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP" };
const backendUrl = process.env.RF_E2E_BACKEND_URL ?? `http://127.0.0.1:${backendPort}`;
process.env.RF_E2E_BACKEND = backendUrl;
const scratch = mkdtempSync(join(tmpdir(), "rf-e2e-"));
const pythonPath = [resolve(repoRoot, "src"), process.env.PYTHONPATH].filter(Boolean).join(delimiter);

export default defineConfig({
  testDir: "e2e",
  timeout: 240_000,
  use: { baseURL: `http://127.0.0.1:${port}`, viewport: { width: 1280, height: 800 } },
  webServer: [
    {
      // The real engine serves the built UI (npm run build first). cwd/env instead of shell syntax so
      // the command also works with cmd.exe on Windows. Its workspace cache is a fresh temp folder.
      command: `uv run raceforge ui --port ${port}`,
      cwd: repoRoot,
      env: { PYTHONPATH: pythonPath, RACEFORGE_WORKSPACE_DIR: join(scratch, "workspace") },
      url: `http://127.0.0.1:${port}/api/v1/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      // Spec 0010: a stand-in for the car runtime's telemetry/teleop WebSocket (Live tab e2e).
      command: `uv run python tests/fake_car_server.py --port ${fakeCarPort} --token ${FAKE_CAR_TOKEN}`,
      cwd: repoRoot,
      env: { PYTHONPATH: pythonPath },
      port: fakeCarPort,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    ...(process.env.RF_E2E_BACKEND_URL
      ? []
      : [
          {
            command: `uv run raceforge backend dev --port ${backendPort} --data ${JSON.stringify(join(scratch, "backend"))} --admin ${E2E_ADMIN.username}:${E2E_ADMIN.password} --admin-totp ${E2E_ADMIN.totp}`,
            cwd: repoRoot,
            env: { PYTHONPATH: pythonPath },
            url: `${backendUrl}/api/v1/status`,
            reuseExistingServer: !process.env.CI,
            timeout: 120_000,
          },
        ]),
  ],
});
