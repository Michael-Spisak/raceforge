import type { Pose } from "../three/CarModel";
import { interpolate, slerp, type TimedPoses } from "./interp";

const id: [number, number, number, number] = [1, 0, 0, 0];
const yaw90: [number, number, number, number] = [Math.SQRT1_2, 0, 0, Math.SQRT1_2];

describe("interpolate", () => {
  const pa: Pose = [[0, 0, 0], id];
  const pb: Pose = [[2, 4, 0], yaw90];
  const a: TimedPoses = { t: 0, poses: { ego: { chassis: pa } } };
  const b: TimedPoses = { t: 1, poses: { ego: { chassis: pb } } };

  it("lerps positions and clamps the parameter", () => {
    const mid = interpolate(a, b, 0.5).ego?.chassis;
    expect(mid?.[0]).toEqual([1, 2, 0]);
    expect(interpolate(a, b, 5).ego?.chassis?.[0]).toEqual([2, 4, 0]);
  });

  it("slerps rotations along the shortest arc", () => {
    const half = slerp(id, yaw90, 0.5);
    const yaw = 2 * Math.atan2(half[3], half[0]);
    expect(yaw).toBeCloseTo(Math.PI / 4, 6);
    const negated = slerp(id, [-yaw90[0], -0, -0, -yaw90[3]], 0.5);
    expect(2 * Math.atan2(negated[3], negated[0])).toBeCloseTo(Math.PI / 4, 6);
  });
});
