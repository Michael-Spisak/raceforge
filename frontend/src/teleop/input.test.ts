import { describe, expect, it } from "vitest";
import { combine, deadZone, fromGamepad, fromKeys, fromTouch, IDLE, nextMessage, type PadState } from "./input";

const limits = { maxSteer: 0.5, speedLimit: 1.2, reverseFactor: 0.5 };

function pad(opts: { x?: number; rt?: number; lt?: number; rb?: boolean; b?: boolean }): PadState {
  const buttons = Array.from({ length: 17 }, () => ({ pressed: false, value: 0 }));
  buttons[5] = { pressed: !!opts.rb, value: opts.rb ? 1 : 0 };
  buttons[1] = { pressed: !!opts.b, value: opts.b ? 1 : 0 };
  buttons[7] = { pressed: (opts.rt ?? 0) > 0, value: opts.rt ?? 0 };
  buttons[6] = { pressed: (opts.lt ?? 0) > 0, value: opts.lt ?? 0 };
  return { axes: [opts.x ?? 0, 0, 0, 0], buttons };
}

describe("dead zone", () => {
  it("removes small values and keeps the full range", () => {
    expect(deadZone(0.1)).toBe(0);
    expect(deadZone(1)).toBe(1);
    expect(deadZone(-1)).toBe(-1);
    expect(deadZone(Number.NaN)).toBe(0);
    expect(deadZone(0.56)).toBeCloseTo(0.5);
  });
});

describe("gamepad", () => {
  it("drives only while RB is held (dead-man)", () => {
    expect(fromGamepad(pad({ rt: 1, x: 1 }), limits)).toMatchObject({ engaged: false, speed: 0, steer: 0 });
    const s = fromGamepad(pad({ rt: 1, x: 1, rb: true }), limits);
    expect(s).toMatchObject({ engaged: true, speed: 1.2 });
    expect(s.steer).toBeCloseTo(-0.5); // stick right = steer right (negative)
  });
  it("reverses with LT at the reduced limit", () => {
    expect(fromGamepad(pad({ lt: 1, rb: true }), limits).speed).toBeCloseTo(-0.6);
  });
  it("B is stop and disengages", () => {
    expect(fromGamepad(pad({ rt: 1, rb: true, b: true }), limits)).toMatchObject({ stop: true, engaged: false });
  });
  it("no pad → idle", () => {
    expect(fromGamepad(null, limits)).toEqual(IDLE);
  });
});

describe("keyboard", () => {
  it("a held drive key is the dead-man", () => {
    expect(fromKeys(new Set(), limits).engaged).toBe(false);
    expect(fromKeys(new Set(["w"]), limits)).toMatchObject({ engaged: true, speed: 1.2, steer: 0 });
    expect(fromKeys(new Set(["arrowup", "arrowleft"]), limits).steer).toBeCloseTo(0.5);
    expect(fromKeys(new Set(["s"]), limits).speed).toBeCloseTo(-0.6);
    expect(fromKeys(new Set(["a"]), limits)).toMatchObject({ engaged: true, speed: 0 }); // steer while standing
  });
  it("space stops", () => {
    expect(fromKeys(new Set(["w", " "]), limits)).toMatchObject({ stop: true, engaged: false, speed: 0 });
  });
});

describe("touch", () => {
  it("drives while the finger is down", () => {
    expect(fromTouch(0, 1, true, limits)).toMatchObject({ engaged: true, speed: 1.2 });
    expect(fromTouch(0, 1, false, limits).engaged).toBe(false);
  });
});

describe("combine and messages", () => {
  it("stop wins over any engaged device", () => {
    const s = combine(fromGamepad(pad({ rt: 1, rb: true }), limits), fromKeys(new Set([" "]), limits));
    expect(s.stop).toBe(true);
    expect(s.engaged).toBe(false);
  });
  it("first engaged device drives", () => {
    const s = combine(fromGamepad(null, limits), fromTouch(0, 0.5, true, limits), fromKeys(new Set(["w"]), limits));
    expect(s.source).toBe("touch");
  });
  it("sends teleop while engaged, one release when letting go", () => {
    expect(nextMessage(fromKeys(new Set(["w"]), limits), false)).toEqual({ type: "teleop", steer: 0, speed: 1.2 });
    expect(nextMessage(IDLE, true)).toEqual({ type: "teleop_release" });
    expect(nextMessage(IDLE, false)).toBeNull();
  });
});
