import { useCallback, useEffect, useRef, useState } from "react";
import { combine, fromGamepad, fromKeys, fromTouch, IDLE, nextMessage, type TeleopLimits, type TeleopMessage, type TeleopSample } from "./input";

export const SEND_PERIOD_MS = 50; // ≥ 6 messages per 300 ms dead-man window

const DRIVE_KEYS = new Set(["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright", " "]);

const TEXT_INPUTS = new Set(["text", "number", "search", "email", "password", "url", "tel"]);

/** Keys typed into a text field are not driving input (checkboxes, sliders and buttons are). */
export function typingInField(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el?.tagName) return false;
  if (el.tagName === "INPUT") return TEXT_INPUTS.has((el as HTMLInputElement).type);
  return el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable;
}

/**
 * Polls gamepad, keyboard and touch every 50 ms while `armed` and sends teleop messages (spec 0010).
 * Leaving the window, disarming or unplugging the gamepad releases at once.
 */
export function useTeleop(opts: {
  armed: boolean;
  limits: TeleopLimits;
  send: (msg: TeleopMessage) => void;
  onStop: () => void;
}) {
  const keys = useRef(new Set<string>());
  const touch = useRef({ x: 0, y: 0, active: false });
  const engaged = useRef(false);
  const stopped = useRef(false);
  const latest = useRef(opts);
  latest.current = opts;
  const [sample, setSample] = useState<TeleopSample>(IDLE);
  const [gamepad, setGamepad] = useState<string | null>(null);

  const setTouch = useCallback((x: number, y: number, active: boolean) => {
    touch.current = { x, y, active };
  }, []);

  useEffect(() => {
    if (!opts.armed) return;
    const down = (e: KeyboardEvent) => {
      const k = e.key.toLowerCase();
      if (!DRIVE_KEYS.has(k) || typingInField(e.target)) return;
      e.preventDefault(); // arrows/space must not scroll the page while driving
      keys.current.add(k);
    };
    const up = (e: KeyboardEvent) => keys.current.delete(e.key.toLowerCase());
    const blur = () => {
      keys.current.clear();
      touch.current = { x: 0, y: 0, active: false };
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
      blur();
    };
  }, [opts.armed]);

  useEffect(() => {
    if (!opts.armed) {
      if (engaged.current) latest.current.send({ type: "teleop_release" });
      engaged.current = false;
      setSample(IDLE);
      return;
    }
    const tick = () => {
      const { limits, send, onStop } = latest.current;
      const pads = typeof navigator.getGamepads === "function" ? navigator.getGamepads() : [];
      const pad = Array.from(pads).find((p) => p && p.connected) ?? null;
      setGamepad(pad ? pad.id : null);
      const s = combine(
        fromGamepad(pad, limits),
        fromTouch(touch.current.x, touch.current.y, touch.current.active, limits),
        fromKeys(keys.current, limits),
      );
      if (s.stop && !stopped.current) onStop();
      stopped.current = s.stop;
      const msg = nextMessage(s, engaged.current);
      if (msg) send(msg);
      engaged.current = s.engaged;
      setSample(s);
    };
    const id = window.setInterval(tick, SEND_PERIOD_MS);
    return () => {
      window.clearInterval(id);
      if (engaged.current) latest.current.send({ type: "teleop_release" });
      engaged.current = false;
    };
  }, [opts.armed]);

  return { sample, gamepad, setTouch };
}
