/** Replay helpers (spec 0034): the frame at a time, speed curves synced to "first moved". */

/** The last frame at or before ``at`` (binary search over sorted times). */
export function frameAt<T>(times: number[], frames: T[], at: number): T | null {
  if (!times.length) return null;
  let lo = 0;
  let hi = times.length - 1;
  if (at < (times[0] ?? 0)) return frames[0] ?? null;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if ((times[mid] ?? Infinity) <= at) lo = mid;
    else hi = mid - 1;
  }
  return frames[lo] ?? null;
}

interface SpeedSource {
  t: number[];
  frames: Record<string, unknown>[];
  moved_s?: number | null;
}

/** (seconds since the car first moved, measured speed) — two drives line up at their start. */
export function speedSeries(run: SpeedSource): [number, number][] {
  const t0 = run.moved_s ?? 0;
  const out: [number, number][] = [];
  run.t.forEach((t, i) => {
    const meas = run.frames[i]?.meas as { speed_m_s?: number | null } | undefined;
    const v = meas?.speed_m_s;
    if (t >= t0 && typeof v === "number") out.push([t - t0, Math.abs(v)]);
  });
  return out;
}
