import { expect, test } from "@playwright/test";
import { createHmac } from "node:crypto";
import { E2E_ADMIN } from "../playwright.config";

/** RFC 6238 TOTP (SHA-1, 6 digits, 30 s) — what an authenticator app shows. */
function totp(secret: string, now = Date.now()): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secret.replace(/=+$/, "")) bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const key = Buffer.from((bits.match(/.{8}/g) ?? []).map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(now / 30000)));
  const h = createHmac("sha1", key).update(counter).digest();
  const o = h.readUInt8(h.length - 1) & 0xf;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

test("team: login with 2FA, workspace, save as version, history, invite", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("workspace-status")).toHaveText(/not logged in|nicht angemeldet/i);
  await page.getByTestId("workspace-status").click();

  await page.getByTestId("login-server").fill(process.env.RF_E2E_BACKEND ?? "");
  await page.getByTestId("login-username").fill(E2E_ADMIN.username);
  await page.getByTestId("login-password").fill(E2E_ADMIN.password);
  await page.getByTestId("login-submit").click();
  await page.getByTestId("login-totp").fill(totp(E2E_ADMIN.totp)); // second step: 2FA
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("team-user")).toContainText("admin");
  await expect(page.getByTestId("workspace-status")).toHaveText(/online/i);

  const name = `E2E ${Date.now()}`;
  await page.getByTestId("ws-new").fill(name);
  await page.getByTestId("ws-create").click();
  await expect(page.getByTestId("ws-select").locator("option:checked")).toHaveText(name);

  // Construct → Save as version
  await page.getByTestId("tab-construct").click();
  await page.getByTestId("field-wheelbase_studs").fill("13");
  await page.getByTestId("save-slug").fill("car-e2e");
  await page.getByTestId("save-message").fill("first e2e car");
  await page.getByTestId("save-version").click();
  await expect(page.getByTestId("save-note")).toContainText("1.0.0", { timeout: 60_000 }); // first save also syncs (slow on Windows CI)
  await page.getByTestId("save-version").click();
  await expect(page.getByTestId("save-note")).toContainText("1.0.1", { timeout: 60_000 });

  // History
  await page.getByTestId("tab-team").click();
  await page.getByTestId("object-car-e2e").click();
  await expect(page.getByTestId("history-item")).toHaveCount(2);
  await expect(page.getByTestId("history-item").last()).toContainText("first e2e car");

  // Admin: invite link
  await page.getByTestId("invite-create").click();
  await expect(page.getByTestId("invite-link")).toContainText("/invite#rfi_");

  // Pair TrackScout (spec 0007 AC6): QR code + a new `trackscout` token with read + edit only
  await page.getByTestId("trackscout-pair").click();
  await expect(page.getByTestId("trackscout-qr")).toBeVisible();
  await expect(page.getByTestId("trackscout-link")).toContainText("raceforge://pair?v=1&d=");
  await expect(page.getByTestId("trackscout-warning")).toBeVisible(); // e2e backend runs on 127.0.0.1
  await expect(page.getByText(/\(trackscout\) · (read, edit|edit, read) ·/)).toBeVisible();

  // Receive by cable without a phone: the Team tab reports why instead of hanging
  await page.getByTestId("trackscout-receive-usb").click();
  await expect(page.getByTestId("trackscout-note")).not.toHaveText(/Receiving|Empfange/, { timeout: 30_000 });
  await expect(page.getByTestId("trackscout-receive-usb")).toBeEnabled();
});
