import type { Pose } from "../three/CarModel";

type V3 = [number, number, number];
type Q4 = [number, number, number, number];

export interface TimedPoses {
  t: number;
  poses: Record<string, Record<string, Pose>>; // car -> body -> pose
}

function lerp3(a: V3, b: V3, u: number): V3 {
  return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u];
}

/** Spherical interpolation of unit quaternions (w, x, y, z), shortest path. */
export function slerp(a: Q4, b: Q4, u: number): Q4 {
  let dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  const c: Q4 = dot < 0 ? [-b[0], -b[1], -b[2], -b[3]] : b;
  dot = Math.abs(dot);
  if (dot > 0.9995) {
    const r: Q4 = [a[0] + (c[0] - a[0]) * u, a[1] + (c[1] - a[1]) * u, a[2] + (c[2] - a[2]) * u, a[3] + (c[3] - a[3]) * u];
    const n = Math.hypot(r[0], r[1], r[2], r[3]);
    return [r[0] / n, r[1] / n, r[2] / n, r[3] / n];
  }
  const theta = Math.acos(dot);
  const s = Math.sin(theta);
  const wa = Math.sin((1 - u) * theta) / s;
  const wb = Math.sin(u * theta) / s;
  return [a[0] * wa + c[0] * wb, a[1] * wa + c[1] * wb, a[2] * wa + c[2] * wb, a[3] * wa + c[3] * wb];
}

/** Interpolates body poses between two frames at time t (clamped to [a.t, b.t]). */
export function interpolate(a: TimedPoses, b: TimedPoses, t: number): Record<string, Record<string, Pose>> {
  const span = b.t - a.t;
  const u = span > 0 ? Math.min(1, Math.max(0, (t - a.t) / span)) : 1;
  const out: Record<string, Record<string, Pose>> = {};
  for (const [car, bodies] of Object.entries(b.poses)) {
    const carOut: Record<string, Pose> = {};
    for (const [body, pb] of Object.entries(bodies)) {
      const pa = a.poses[car]?.[body] ?? pb;
      carOut[body] = [lerp3(pa[0], pb[0], u), slerp(pa[1], pb[1], u)];
    }
    out[car] = carOut;
  }
  return out;
}
