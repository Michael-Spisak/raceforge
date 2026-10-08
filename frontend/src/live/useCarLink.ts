import { useCallback, useEffect, useRef, useState } from "react";
import { carSocketUrl } from "../api/client";

/** Messages from the car runtime (spec 0005, forwarded unchanged) and from the engine relay (spec 0010). */
export interface CarFrame {
  state?: string;
  mode?: string;
  faults?: string[];
  seq?: number;
  cmd?: { steering_rad: number; speed_m_s: number };
  meas?: { steering_rad?: number | null; speed_m_s?: number | null };
  power?: { ev3_battery_v?: number | null; board_battery_v?: number | null; motor_battery_v?: number | null };
  loop?: { rate_hz: number; jitter_ms?: number; deadline_misses?: number };
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

  const addEvent = (kind: string, text: string) =>
    setEvents((e) => [{ at: Date.now(), kind, detail: text }, ...e].slice(0, 200));

  const disconnect = useCallback(() => {
    ws.current?.close();
    ws.current = null;
  }, []);

  const connect = useCallback((url: string, token: string) => {
    disconnect();
    setState("connecting");
    setDetail("");
    setFrame(null);
    setRtt(null);
    setCar(null);
    const socket = new WebSocket(carSocketUrl());
    ws.current = socket;
    socket.onopen = () => socket.send(JSON.stringify({ type: "connect", url, token: token || null }));
    socket.onmessage = (ev: MessageEvent<string>) => {
      const msg = JSON.parse(ev.data) as Record<string, unknown>;
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
          break;
        case "event":
          addEvent(String(msg.kind), String(msg.detail ?? ""));
          break;
        case "ack":
          if (!msg.ok) addEvent(`${String(msg.cmd)} refused`, String(msg.detail ?? ""));
          break;
      }
    };
    socket.onclose = () => setState((s) => (s === "error" ? s : "closed"));
  }, [disconnect]);

  const send = useCallback((msg: object) => {
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(msg));
  }, []);

  useEffect(() => disconnect, [disconnect]);
  return { state, detail, car, frame, rtt, events, connect, disconnect, send };
}
