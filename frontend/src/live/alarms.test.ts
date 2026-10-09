import { describe, expect, it } from "vitest";
import { DEFAULT_THRESHOLDS, Rolling, alarms } from "./alarms";

describe("rolling history (spec 0027 AC4)", () => {
  it("keeps only the last span and groups states", () => {
    const r = new Rolling(1000);
    r.push({ state: "run", meas: { speed_m_s: 1 } }, 0);
    r.push({ state: "run", meas: { speed_m_s: null } }, 500);
    r.push({ state: "stop", meas: { speed_m_s: 0 } }, 1200);
    expect(r.samples.map((s) => s.at)).toEqual([500, 1200]);
    expect(r.series((f) => f.meas?.speed_m_s)).toEqual([[1200, 0]]);
    expect(r.states()).toEqual([["run", 500, 500], ["stop", 1200, 1200]]);
  });
});

describe("alarms (spec 0027 AC4)", () => {
  it("raises on thresholds and new faults, never on missing values", () => {
    expect(alarms(null, null, DEFAULT_THRESHOLDS)).toEqual([]);
    expect(alarms({ state: "run" }, null, DEFAULT_THRESHOLDS)).toEqual([]);
    const f = { loop: { rate_hz: 30 }, power: { ev3_battery_v: 6.5, board_cpu_temp_c: 90 }, faults: ["lidar"] };
    expect(alarms(f, 150, DEFAULT_THRESHOLDS).map((a) => a.key)).toEqual(["loop", "battery", "cpu", "latency", "fault"]);
    expect(alarms(f, 20, DEFAULT_THRESHOLDS, ["lidar"]).map((a) => a.key)).not.toContain("fault");
  });
});
