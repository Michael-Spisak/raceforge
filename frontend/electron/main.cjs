// Electron shell (spec 0008): starts the Python engine as a sidecar and shows the UI it serves.
// Dev setup: run from frontend/ after `uv sync` (repo root) and `npm run build`.
// `electron . --smoke` starts, waits for the UI to load, checks /api/v1/health and quits (CI smoke test).
const { app, BrowserWindow, dialog } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..", "..");
const smoke = process.argv.includes("--smoke");
let engine = null;

function startEngine() {
  return new Promise((resolve, reject) => {
    const env = { ...process.env, PYTHONPATH: path.join(repoRoot, "src") };
    engine = spawn("uv", ["run", "raceforge", "ui", "--port", "0"], { cwd: repoRoot, env, shell: process.platform === "win32" });
    const timer = setTimeout(() => reject(new Error("engine did not start within 60 s")), 60_000);
    engine.stdout.on("data", (chunk) => {
      const match = /RACEFORGE_ENGINE_URL=(\S+)/.exec(String(chunk));
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
    engine.stderr.on("data", (chunk) => process.stderr.write(chunk));
    engine.on("exit", (code) => reject(new Error(`engine exited with code ${code}`)));
  });
}

async function waitForHealth(url) {
  for (let i = 0; i < 100; i++) {
    try {
      const res = await fetch(`${url}api/v1/health`);
      if (res.ok) return await res.json();
    } catch {
      /* not ready yet */
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("engine health check failed");
}

function stopEngine() {
  if (engine && !engine.killed) engine.kill();
}

app.whenReady().then(async () => {
  try {
    const url = await startEngine();
    const health = await waitForHealth(url);
    const win = new BrowserWindow({ width: 1400, height: 900, show: !smoke, title: "RaceForge" });
    await win.loadURL(url);
    if (smoke) {
      const title = await win.webContents.executeJavaScript("document.title");
      console.log(`smoke ok: title=${title} engine=${health.version}`);
      stopEngine();
      app.exit(title === "RaceForge" ? 0 : 1);
    }
  } catch (err) {
    stopEngine();
    if (!smoke) dialog.showErrorBox("RaceForge engine could not start", String(err));
    console.error(String(err));
    app.exit(1);
  }
});

app.on("window-all-closed", () => {
  stopEngine();
  app.quit();
});
app.on("before-quit", stopEngine);
