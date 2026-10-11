import { describe, expect, it } from "vitest";
import { frameAt, speedSeries } from "./timeline";

describe("replay timeline (spec 0034)", () => {
  it("finds the frame at a time", () => {
    const t = [0, 0.1, 0.2, 0.5];
    const f = ["a", "b", "c", "d"];
    expect(frameAt(t, f, -1)).toBe("a");
    expect(frameAt(t, f, 0.15)).toBe("b");
    expect(frameAt(t, f, 0.2)).toBe("c");
    expect(frameAt(t, f, 9)).toBe("d");
    expect(frameAt([], [], 1)).toBeNull();
  });

  it("syncs speed curves to the moment the car first moved", () => {
    const run = {
      t: [0, 1, 2, 3],
      moved_s: 1,
      frames: [{ meas: { speed_m_s: 0 } }, { meas: { speed_m_s: 0.2 } }, { meas: { speed_m_s: -0.5 } }, { meas: {} }],
    };
    expect(speedSeries(run)).toEqual([[0, 0.2], [1, 0.5]]);
  });
});
