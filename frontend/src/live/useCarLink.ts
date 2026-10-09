import { useCallback, useEffect, useRef, useState } from "react";
import { carSocketUrl, liveWatchUrl } from "../api/client";

/** Messages from the car runtime (spec 0005, forwarded unchanged) and from the engine relay (spec 0010). */
export interface CarFrame {
  state?: string;
  mode?: string;
  faults?: string[];
  seq?: number;
  cmd?: { steering_rad: number; speed_m_s: number };
  meas?: { steering_rad?: number | null; speed_m_s?: number | null; yaw_rate_rad_s?: number | null; sensors?: Record<string, SensorReading> };
  pose_est?: { pose: { x: number; y: number; yaw: number }; confidence: number } | null;
  power?: { ev3_battery_v?: number | null; board_battery_v?: number | null; motor_battery_v?: number | null; board_cpu_temp_c?: number | null; board_cpu_load?: number | null };
  loop?: { rate_hz: number; jitter_ms?: number; deadline_misses?: number };
  channels?: Record<string, number | string | boolean>;
}

export type SensorReading =
  | { kind: "range"; distance_m: number | null }
  | { kind: "range_array"; angle_min_rad: number; angle_increment_rad: number; ranges: (number | null)[] }
  | { kind: "imu" | "bool" | "camera_frame"; [k: string]: unknown };

/** Team relay state (spec 0027): off | sharing | offline; ``run`` = saved run log slug. */
export interface ShareInfo {
  state: "off" | "sharing" | "offline";
  session: string | null;
  run?: string | null;
}

/** Answer of the car's race-mode radio pre-check (spec 0030). */
export interface RadioCheck {
  ok: boolean;
  violations: string[];
  unsupported?: boolean;
  at: number;
}

export type LinkState = "idle" | "connecting" | "connected" | "error" | "closed";

export interface CarEvent {
  at: number;
  kind: string;
  detail: string;
}

export const LATENCY_WARN_MS = 100;

export function useCarLink() {
  const ws = useRef<WebSocket | null>(null);
  const [state, setState] = useState<LinkState>("idle");
  const [detail, setDetail] = useState("");
  const [car, setCar] = useState<{ name: string; mode: string } | null>(null);
  const [frame, setFrame] = useState<CarFrame | null>(null);
  const [rtt, setRtt] = useState<number | null>(null);
  const [events, setEvents] = useState<CarEvent[]>([]);
  const [share, setShare] = useState<ShareInfo>({ state: "off", session: null });
  const [watching, setWatching] = useState<string | null>(null);
  const [lastFrameAt, setLastFrameAt] = useState(0);
  const [radio, setRadio] = useState<RadioCheck | null>(null);

  const addEvent = (kind: string, text: string) =>
    setEvents((e) => [{ at: Date.now(), kind, detail: text }, ...e].slice(0, 200));

  const disconnect = useCallback(() => {
    if (!ws.current) return;
    ws.current.close();
    ws.current = null;
    setState((s) => (s === "error" ? s : "closed"));
  }, []);

  const open = useCallback((socketUrl: string, first: object | null, session: string | null) => {
    disconnect();
    setState("connecting");
    setDetail("");
    setFrame(null);
    setRtt(null);
    setCar(null);
    setShare({ state: "off", session: null });
    setRadio(null);
    setWatching(session);
    const socket = new WebSocket(socketUrl);
    ws.current = socket;
    socket.onopen = () => {
      if (first) socket.send(JSON.stringify(first));
      else setState("connected");
    };
    socket.onmessage = (ev: MessageEvent<string>) => {
      if (ws.current !== socket) return; // a newer connection replaced this one
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(ev.data) as Record<string, unknown>;
      } catch {
        return;
      }
      switch (msg.type) {
        case "link":
          setState(msg.state as LinkState);
          if (typeof msg.rtt_ms === "number") setRtt(msg.rtt_ms);
          if (typeof msg.detail === "string") setDetail(msg.detail);
          break;
        case "hello":
          setCar({ name: String(msg.car), mode: String(msg.mode) });
          break;
        case "telemetry":
          setFrame(msg.frame as CarFrame);
          setLastFrameAt(Date.now());
          break;
        case "share":
          setShare({ state: msg.state as ShareInfo["state"], session: (msg.session as string | null) ?? null });
          break;
        case "run":
          setShare((s) => ({ ...s, run: (msg.saved as string | null) ?? null }));
          if (!msg.saved && msg.detail) addEvent("run log", String(msg.detail));
          break;
        case "session":
          setCar({ name: String(msg.car), mode: "test" });
          setDetail(String(msg.publisher ?? ""));
          break;
        case "end":
          setState("closed");
          if (msg.detail) setDetail(String(msg.detail));
          break;
        case "event":
          addEvent(String(msg.kind), String(msg.detail ?? ""));
          break;
        case "radio_check":
          setRadio({ ok: msg.ok === true, violations: (msg.violations as string[] | undefined) ?? [], at: Date.now() });
          break;
        case "ack":
          if (msg.cmd === "radio_check" && !msg.ok) setRadio({ ok: false, violations: [String(msg.detail ?? "")], unsupported: true, at: Date.now() });
          else if (!msg.ok) addEvent(`${String(msg.cmd)} refused`, String(msg.detail ?? ""));
          break;
      }
    };
    socket.onclose = () => {
      if (ws.current === socket) setState((s) => (s === "error" ? s : "closed"));
    };
  }, [disconnect]);

  const connect = useCallback((url: string, token: string, shareWithTeam = true, shareRateHz = 10) =>
    open(carSocketUrl(), { type: "connect", url, token: token || null, share: shareWithTeam, share_rate_hz: shareRateHz }, null), [open]);
  /** Watch a teammate's car through the team relay (read-only). */
  const watch = useCallback((session: string) => open(liveWatchUrl(session), null, session), [open]);

  const send = useCallback((msg: object) => {
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(msg));
  }, []);

  useEffect(() => disconnect, [disconnect]);
  const pushEvent = useCallback((e: CarEvent) => setEvents((list) => [e, ...list].slice(0, 200)), []);
  return { state, detail, car, frame, lastFrameAt, rtt, events, share, watching, radio, connect, watch, disconnect, send, pushEvent };
}
