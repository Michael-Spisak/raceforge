import { ApiError, needsTotp, versionLabel } from "../api/client";
import { badgeOf } from "./workspace";

const status = { logged_in: true, online: true, server_url: "x", user: null, workspace: null, pending: 0, conflicts: 0, last_sync: null };

describe("workspace helpers", () => {
  it("shows the badge state", () => {
    expect(badgeOf(null)).toBe("none");
    expect(badgeOf({ ...status, logged_in: false })).toBe("none");
    expect(badgeOf(status)).toBe("online");
    expect(badgeOf({ ...status, online: false })).toBe("offline");
  });

  it("labels local-only versions", () => {
    expect(versionLabel({ semver: "1.0.2", pending: false })).toBe("1.0.2");
    expect(versionLabel({ semver: null, pending: true })).toBeNull();
  });

  it("detects the 2FA step", () => {
    expect(needsTotp(new ApiError("totp_required", 401))).toBe(true);
    expect(needsTotp(new ApiError("wrong username or password", 401))).toBe(false);
    expect(needsTotp(new Error("x"))).toBe(false);
  });
});
