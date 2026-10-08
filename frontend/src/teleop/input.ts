/**
 * Teleop input mapping (spec 0010): gamepad, keyboard and touch joystick → {steer rad, speed m/s}.
 * Pure functions so they are unit-tested; `useTeleop` polls the devices and sends the result.
 *
 * Dead-man: a sample is `engaged` only while the operator actively holds something (gamepad RB, a drive key,
 * a finger on the joystick). STOP (Space, gamepad B, the STOP button) always wins.
 */

export interface TeleopLimits {
  /** Steering lock of the car (rad). */
  maxSteer: number;
  /** Session speed limit (m/s), set on the teleop panel. */
  speedLimit: number;
  /** Reverse is limited to this fraction of the forward limit. */
  reverseFactor?: number;
}

export interface TeleopSample {
  source: "gamepad" | "keyboard" | "touch" | "none";
  engaged: boolean;
  stop: boolean;
  steer: number;
  speed: number;
}

export const IDLE: TeleopSample = { source: "none", engaged: false, stop: false, steer: 0, speed: 0 };

export const DEAD_ZONE = 0.12;

/** Maps |v| ≤ dz to 0 and rescales the rest to keep the full range. */
export function deadZone(v: number, dz = DEAD_ZONE): number {
  if (!Number.isFinite(v) || Math.abs(v) <= dz) return 0;
  return (Math.sign(v) * (Math.abs(v) - dz)) / (1 - dz);
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

function scale(steerIn: number, throttle: number, limits: TeleopLimits): { steer: number; speed: number } {
  const reverse = limits.reverseFactor ?? 0.5;
  const t = clamp(throttle, -1, 1);
  // Positive steer = left (RaceForge convention); stick right (+x) steers right.
  return {
    steer: clamp(-steerIn, -1, 1) * limits.maxSteer || 0, // `|| 0` turns -0 into 0
    speed: t >= 0 ? t * limits.speedLimit : t * limits.speedLimit * reverse,
  };
}

/** Xbox standard mapping: axes[0] = left stick X, buttons 6/7 = LT/RT, 5 = RB (dead-man), 1 = B (stop). */
export interface PadState {
  axes: readonly number[];
  buttons: readonly { pressed: boolean; value: number }[];
}

export function fromGamepad(pad: PadState | null, limits: TeleopLimits): TeleopSample {
  if (!pad) return IDLE;
  const b = (i: number) => pad.buttons[i];
  const stop = b(1)?.pressed ?? false;
  const engaged = (b(5)?.pressed ?? false) && !stop;
  const throttle = (b(7)?.value ?? 0) - (b(6)?.value ?? 0);
  const { steer, speed } = scale(deadZone(pad.axes[0] ?? 0), deadZone(throttle, 0.05), limits);
  return { source: "gamepad", engaged, stop, steer: engaged ? steer : 0, speed: engaged ? speed : 0 };
}

const FORWARD = ["w", "arrowup"];
const BACK = ["s", "arrowdown"];
const LEFT = ["a", "arrowleft"];
const RIGHT = ["d", "arrowright"];

/** Keys are lower-case `KeyboardEvent.key` values; holding any drive key is the dead-man. */
export function fromKeys(keys: ReadonlySet<string>, limits: TeleopLimits): TeleopSample {
  const has = (list: string[]) => list.some((k) => keys.has(k));
  const stop = keys.has(" ");
  const throttle = (has(FORWARD) ? 1 : 0) - (has(BACK) ? 1 : 0);
  const steerIn = (has(RIGHT) ? 1 : 0) - (has(LEFT) ? 1 : 0);
  const engaged = !stop && (has(FORWARD) || has(BACK) || has(LEFT) || has(RIGHT));
  const { steer, speed } = scale(steerIn, throttle, limits);
  return { source: "keyboard", engaged, stop, steer: engaged ? steer : 0, speed: engaged ? speed : 0 };
}

/** Touch joystick: x right / y up in [-1, 1]; `active` while a finger is down. */
export function fromTouch(x: number, y: number, active: boolean, limits: TeleopLimits): TeleopSample {
  if (!active) return { ...IDLE, source: "touch" };
  const { steer, speed } = scale(deadZone(x, 0.08), deadZone(y, 0.08), limits);
  return { source: "touch", engaged: true, stop: false, steer, speed };
}

/** STOP from any device wins; otherwise the first engaged device (gamepad, touch, keyboard). */
export function combine(...samples: TeleopSample[]): TeleopSample {
  if (samples.some((s) => s.stop)) return { ...IDLE, stop: true, source: samples.find((s) => s.stop)?.source ?? "none" };
  return samples.find((s) => s.engaged) ?? IDLE;
}

export type TeleopMessage =
  | { type: "teleop"; steer: number; speed: number }
  | { type: "teleop_release" };

/**
 * What to send this tick: a teleop command while engaged, one `teleop_release` when the operator lets go,
 * nothing while idle. STOP is handled separately (it is a different message for sim and car).
 */
export function nextMessage(sample: TeleopSample, wasEngaged: boolean): TeleopMessage | null {
  if (sample.engaged) {
    return { type: "teleop", steer: Math.round(sample.steer * 1e4) / 1e4, speed: Math.round(sample.speed * 1e3) / 1e3 };
  }
  return wasEngaged ? { type: "teleop_release" } : null;
}
