import { toParams } from "./ConstructScreen";
import { cutaway } from "../three/TrackModel";

describe("construct form", () => {
  it("adds the LiDAR to the default ultrasonic sensors when ticked", () => {
    const without = toParams({ wheelbase_studs: 15 });
    expect(without.sensors?.map((s) => s.kind)).toEqual(["ev3_ultrasonic", "ev3_ultrasonic", "ev3_ultrasonic"]);
    const withLidar = toParams({ lidar: true });
    expect(withLidar.sensors?.at(-1)).toMatchObject({ kind: "lidar_2d", preset: "top" });
    expect("lidar" in withLidar).toBe(false);
  });
});

describe("cutaway", () => {
  it("cuts tall boxes to 30 cm and leaves low ones alone", () => {
    const wall = { kind: "box" as const, pos: [0, 0, 1.25] as [number, number, number], quat: [1, 0, 0, 0] as [number, number, number, number],
      size: [1, 0.02, 1.25] as [number, number, number], color: "#fff", opacity: 1, surface: "wall" };
    const cut = cutaway(wall, true);
    expect(cut.pos[2] + cut.size[2]).toBeCloseTo(0.3);
    expect(cut.pos[2] - cut.size[2]).toBeCloseTo(0);
    expect(cutaway(wall, false)).toBe(wall);
    const bin = { ...wall, pos: [0, 0, 0.1] as [number, number, number], size: [0.2, 0.2, 0.1] as [number, number, number] };
    expect(cutaway(bin, true)).toBe(bin);
  });
});
