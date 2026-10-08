import { expect, test } from "@playwright/test";

test("construct -> simulate -> replay", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("engine-status")).toHaveText(/online/i);

  // Construct: changing the wheelbase updates the derived data.
  await page.getByTestId("tab-construct").click();
  const wheelbase = page.getByTestId("derived-wheelbase");
  await expect(wheelbase).toHaveText("120 mm");
  await page.getByTestId("field-wheelbase_studs").fill("13");
  await expect(wheelbase).toHaveText("104 mm");
  await expect(page.getByTestId("construct-viewport").locator("canvas")).toBeVisible();

  // Invalid value: the engine's "nearest valid" hint appears next to the field.
  await page.getByTestId("field-wheelbase_studs").fill("40");
  await expect(page.getByRole("alert")).toContainText("nearest valid");
  await page.getByTestId("field-wheelbase_studs").fill("15");

  // Simulate one lap with the centering template at max speed, recording the run.
  await page.getByTestId("tab-simulate").click();
  await page.getByTestId("sim-speed").selectOption("1000");
  await page.getByTestId("sim-record").check();
  await page.getByTestId("sim-start").click();
  await expect(page.getByTestId("sim-viewport").locator("canvas")).toBeVisible();
  await expect(page.getByTestId("sim-result")).toBeVisible({ timeout: 180_000 });
  await expect(page.getByTestId("lap-times")).toHaveText(/\d+\.\d s/);
  const recorded = (await page.getByTestId("record-path").textContent()) ?? "";

  // Replay the recording (path is relative to the engine's working directory).
  await page.getByTestId("tab-replay").click();
  await page.getByTestId("replay-path").fill(recorded);
  await page.getByTestId("replay-open").click();
  await expect(page.getByTestId("replay-summary")).toBeVisible();
  await expect(page.getByTestId("trajectory")).toBeVisible();
});
