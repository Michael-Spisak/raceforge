import { expect, test } from "@playwright/test";
import { FAKE_CAR_TOKEN, fakeCarPort } from "../playwright.config";

test("live: connect to a car, drive with teleop, release, STOP (spec 0010 B)", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("tab-live").click();
  await page.getByTestId("car-url").fill(`ws://127.0.0.1:${fakeCarPort}`);
  await page.getByTestId("car-token").fill(FAKE_CAR_TOKEN);
  await page.getByTestId("car-connect").click();
  await expect(page.getByTestId("car-link-state")).toHaveText(/connected|verbunden/);
  await expect(page.getByTestId("car-status")).toContainText("fake-car");
  await expect(page.getByTestId("car-state")).toHaveText("run");
  await expect(page.getByTestId("car-rtt")).toHaveText(/\d+ ms/);

  await page.getByTestId("teleop-arm").check();
  await page.keyboard.down("w");
  await expect(page.getByTestId("car-state")).toHaveText("teleop");
  await page.keyboard.up("w");
  await expect(page.getByTestId("car-state")).toHaveText("run"); // released → controller again

  await page.getByTestId("teleop-stop").click();
  await expect(page.getByTestId("car-state")).toHaveText("stop");
  await expect(page.getByTestId("car-events")).toContainText("operator_stop");
});

test("live: wrong token is reported", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("tab-live").click();
  await page.getByTestId("car-url").fill(`ws://127.0.0.1:${fakeCarPort}`);
  await page.getByTestId("car-token").fill("wrong-token-xxxxxxxx");
  await page.getByTestId("car-connect").click();
  await expect(page.getByTestId("car-link-state")).toHaveText(/error|closed|Fehler|beendet/);
});
