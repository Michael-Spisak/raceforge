/** Dashboard logic for the Live tab (spec 0027): rolling history and alarms. No React here. */
import type { CarFrame } from "./useCarLink";

export interface Sample {
  at: number; // ms (receive time)
  frame: CarFrame;
}

/** Keeps the samples of the last ``spanMs`` (oldest first). */
export class Rolling {
  readonly samples: Sample[] = [];
  constructor(readonly spanMs: number) {}

  push(frame: CarFrame, at: number): void {
    this.samples.push({ at, frame });
    const cut = at - this.spanMs;
    let drop = 0;
    while (drop < this.samples.length && (this.samples[drop]?.at ?? Infinity) < cut) drop++;
    if (drop) this.samples.splice(0, drop);
  }

  series(pick: (f: CarFrame) => number | null | undefined): [number, number][] {
    const out: [number, number][] = [];
    for (const s of this.samples) {
      const v = pick(s.frame);
      if (v != null && Number.isFinite(v)) out.push([s.at, v]);
    }
    return out;
  }

  /** Consecutive runs of the same state: [state, from, to]. */
  states(): [string, number, number][] {
    const out: [string, number, number][] = [];
    for (const s of this.samples) {
      const st = s.frame.state ?? "–";
      const last = out[out.length - 1];
      if (last && last[0] === st) last[2] = s.at;
      else out.push([st, s.at, s.at]);
    }
    return out;
  }
}

export interface Thresholds {
  minLoopHz: number;
  minBatteryV: number;
  maxCpuC: number;
  maxRttMs: number;
}

export const DEFAULT_THRESHOLDS: Thresholds = { minLoopHz: 40, minBatteryV: 7.0, maxCpuC: 85, maxRttMs: 100 };

export type AlarmKey = "loop" | "battery" | "cpu" | "latency" | "fault";

export interface Alarm {
  key: AlarmKey;
  value: string;
}

/** The alarms raised by this frame. Missing values never raise one; faults only when new. */
export function alarms(frame: CarFrame | null, rtt: number | null, t: Thresholds, prevFaults: string[] = []): Alarm[] {
  const out: Alarm[] = [];
  const rate = frame?.loop?.rate_hz;
  if (rate != null && rate < t.minLoopHz) out.push({ key: "loop", value: `${rate.toFixed(0)} Hz` });
  const v = frame?.power?.ev3_battery_v ?? frame?.power?.motor_battery_v;
  if (v != null && v < t.minBatteryV) out.push({ key: "battery", value: `${v.toFixed(1)} V` });
  const cpu = frame?.power?.board_cpu_temp_c;
  if (cpu != null && cpu > t.maxCpuC) out.push({ key: "cpu", value: `${cpu.toFixed(0)} °C` });
  if (rtt != null && rtt > t.maxRttMs) out.push({ key: "latency", value: `${rtt.toFixed(0)} ms` });
  const fresh = (frame?.faults ?? []).filter((f) => !prevFaults.includes(f));
  if (fresh.length) out.push({ key: "fault", value: fresh.join(", ") });
  return out;
}

export function loadThresholds(): Thresholds {
  try {
    return { ...DEFAULT_THRESHOLDS, ...(JSON.parse(localStorage.getItem("rf.live.thresholds") ?? "{}") as Partial<Thresholds>) };
  } catch {
    return DEFAULT_THRESHOLDS;
  }
}

export function saveThresholds(t: Thresholds): void {
  try {
    localStorage.setItem("rf.live.thresholds", JSON.stringify(t));
  } catch {
    /* private window: keep for this session only */
  }
}

/** A short beep (WebAudio); silently does nothing where audio is unavailable. */
export function beep(): void {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    osc.frequency.value = 880;
    osc.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.15);
    osc.onended = () => void ctx.close();
  } catch {
    /* no audio */
  }
}
