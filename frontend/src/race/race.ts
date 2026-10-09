/** Race Control logic (spec 0031): race clock, laps per car, incidents, ranking. No React here. */

export type CarEntry = {
  name: string;
  laps: number[]; // lap end times (ms since the start signal)
  dnf: boolean;
  incidents: { at: number; text: string }[];
};

export interface Race {
  laps: number;
  startedAt: number | null; // epoch ms of the start signal
  cars: CarEntry[];
}

export function newRace(names: string[], laps: number): Race {
  return { laps, startedAt: null, cars: names.map((name) => ({ name, laps: [], dnf: false, incidents: [] })) };
}

export function finished(c: CarEntry, laps: number): boolean {
  return c.laps.length >= laps;
}

/** Count a lap for car ``i`` at ``now`` (ignored before the start, after the finish, or for a DNF). */
export function lap(r: Race, i: number, now: number): Race {
  const c = r.cars[i];
  if (r.startedAt == null || !c || c.dnf || finished(c, r.laps)) return r;
  const cars = r.cars.map((x, j) => (j === i ? { ...x, laps: [...x.laps, now - (r.startedAt ?? now)] } : x));
  return { ...r, cars };
}

/** Undo the last lap of car ``i`` (a mis-tap). */
export function undoLap(r: Race, i: number): Race {
  return { ...r, cars: r.cars.map((x, j) => (j === i ? { ...x, laps: x.laps.slice(0, -1) } : x)) };
}

export function setDnf(r: Race, i: number, dnf: boolean): Race {
  return { ...r, cars: r.cars.map((x, j) => (j === i ? { ...x, dnf } : x)) };
}

export function incident(r: Race, i: number, now: number, text: string): Race {
  const at = r.startedAt == null ? 0 : now - r.startedAt;
  return { ...r, cars: r.cars.map((x, j) => (j === i ? { ...x, incidents: [...x.incidents, { at, text }] } : x)) };
}

export type Standing = {
  name: string;
  laps: number;
  time_ms: number | null; // finish time, or the time of the last lap
  best_lap_ms: number | null;
  status: "finished" | "racing" | "dnf";
};

/** Finished cars by time, then racing cars by laps (more first) and time, then DNFs. */
export function standings(r: Race): Standing[] {
  const rows: Standing[] = r.cars.map((c) => {
    const splits = c.laps.map((t, k) => t - (k > 0 ? (c.laps[k - 1] ?? 0) : 0));
    return {
      name: c.name,
      laps: c.laps.length,
      time_ms: c.laps.length ? (c.laps[c.laps.length - 1] ?? null) : null,
      best_lap_ms: splits.length ? Math.min(...splits) : null,
      status: c.dnf ? "dnf" : finished(c, r.laps) ? "finished" : "racing",
    };
  });
  const rank = (s: Standing) => (s.status === "finished" ? 0 : s.status === "racing" ? 1 : 2);
  return rows.sort((a, b) => rank(a) - rank(b) || b.laps - a.laps || (a.time_ms ?? Infinity) - (b.time_ms ?? Infinity));
}

export function fmtMs(ms: number | null): string {
  if (ms == null) return "–";
  const s = ms / 1000;
  const m = Math.floor(s / 60);
  return m ? `${m}:${(s - m * 60).toFixed(2).padStart(5, "0")}` : s.toFixed(2);
}
