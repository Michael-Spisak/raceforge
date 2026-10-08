import { expect, test } from "@playwright/test";

const distance = async (page: import("@playwright/test").Page) =>
  Number((await page.getByTestId("sim-status").innerText()).match(/([\d.]+) m/)?.[1] ?? "0");

test("teleop: drive the simulated car with the keyboard (spec 0010 AC3)", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("tab-simulate").click();
  await page.getByTestId("sim-controller").selectOption("none");
  await page.getByTestId("sim-start").click();
  await expect(page.getByTestId("teleop-panel")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("sim-state")).toHaveText("run");
  const before = await distance(page);

  await page.getByTestId("teleop-arm").check();
  await page.keyboard.down("w");
  await expect(page.getByTestId("sim-state")).toHaveText("teleop");
  await expect(page.getByTestId("teleop-speed")).not.toHaveText("0.00 m/s");
  await expect.poll(() => distance(page), { timeout: 10_000 }).toBeGreaterThan(before + 0.3);
  await page.keyboard.up("w");

  // released: the controller ("none") takes over again and the car comes to rest (it may coast a bit)
  await expect(page.getByTestId("sim-state")).toHaveText("run");
  await expect
    .poll(async () => {
      const a = await distance(page);
      await page.waitForTimeout(500);
      return (await distance(page)) - a;
    }, { timeout: 15_000 })
    .toBeLessThan(0.02);

  // STOP latches
  await page.getByTestId("teleop-stop").click();
  await expect(page.getByTestId("sim-state")).toHaveText("stop");
});
