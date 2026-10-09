import { describe, expect, it } from "vitest";
import { type Race, fmtMs, incident, lap, newRace, setDnf, standings, undoLap } from "./race";

describe("race control (spec 0031)", () => {
  it("counts laps only after the start and until the finish", () => {
    let r = newRace(["a", "b"], 2);
    r = lap(r, 0, 1000); // before the start: ignored
    expect(r.cars[0]?.laps).toEqual([]);
    r = { ...r, startedAt: 1000 };
    r = lap(r, 0, 11000);
    r = lap(r, 0, 20000);
    r = lap(r, 0, 30000); // already finished: ignored
    expect(r.cars[0]?.laps).toEqual([10000, 19000]);
    expect(undoLap(r, 0).cars[0]?.laps).toEqual([10000]);
  });

  it("ranks finished cars by time, then racing by laps, then DNFs", () => {
    let r: Race = { ...newRace(["slow", "fast", "out", "half"], 2), startedAt: 0 };
    r = lap(lap(r, 0, 10000), 0, 25000);
    r = lap(lap(r, 1, 9000), 1, 18000);
    r = setDnf(lap(r, 2, 5000), 2, true);
    r = lap(r, 3, 12000);
    r = incident(r, 0, 4000, "scraped the wall");
    const s = standings(r);
    expect(s.map((x) => x.name)).toEqual(["fast", "slow", "half", "out"]);
    expect(s[0]?.best_lap_ms).toBe(9000);
    expect(r.cars[0]?.incidents).toEqual([{ at: 4000, text: "scraped the wall" }]);
  });

  it("formats times", () => {
    expect(fmtMs(null)).toBe("–");
    expect(fmtMs(9050)).toBe("9.05");
    expect(fmtMs(75300)).toBe("1:15.30");
  });
});
