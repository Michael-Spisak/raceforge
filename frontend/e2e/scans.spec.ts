import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = fileURLToPath(new URL("../..", import.meta.url));

/** A synthetic TrackScout pass (same generator as the Python importer tests). */
function syntheticPass(): string {
  const file = join(mkdtempSync(join(tmpdir(), "rf-scan-")), "corridor.tscan");
  const code = "import sys; from pathlib import Path; from tests.capture.synth import make_pass; make_pass(Path(sys.argv[1]))";
  execFileSync("uv", ["run", "python", "-c", code, file], {
    cwd: repoRoot,
    env: { ...process.env, PYTHONPATH: [repoRoot, resolve(repoRoot, "src"), process.env.PYTHONPATH].filter(Boolean).join(delimiter) },
  });
  return file;
}

test("scans: open a .tscan by path and look at it in 3D (spec 0009 AC5)", async ({ page }) => {
  const file = syntheticPass();
  await page.goto("/");
  await page.getByTestId("tab-scans").click();
  await page.getByTestId("scan-path").fill(file);
  await page.getByTestId("scan-open").click();

  const summary = page.getByTestId("scan-summary");
  await expect(summary).toContainText("20"); // frames
  await expect(summary).toContainText("3 of 3 triangles");
  await expect(page.getByTestId("scan-viewport").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("scan-legend")).toContainText("wall");

  // toggles keep the view alive; German strings exist
  await page.getByTestId("scan-hide-ceiling").uncheck();
  await page.getByTestId("scan-cut").check();
  await page.getByTestId("scan-show-mesh").uncheck();
  await expect(page.getByTestId("scan-viewport").locator("canvas")).toBeVisible();
  await page.getByRole("combobox", { name: /language|sprache/i }).selectOption("de");
  await expect(page.getByTestId("scan-legend")).toContainText("Wand");
  await page.getByRole("combobox", { name: /language|sprache/i }).selectOption("en");
});
