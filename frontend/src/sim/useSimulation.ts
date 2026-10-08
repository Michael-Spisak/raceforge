import { useCallback, useEffect, useRef, useState } from "react";
import {
  type FrameMessage,
  type ResultMessage,
  type SceneMessage,
  type ServerMessage,
  type SimStart,
  simSocketUrl,
} from "../api/client";
import type { Pose } from "../three/CarModel";
import type { TimedPoses } from "./interp";

export type SimStatus = "idle" | "connecting" | "running" | "paused" | "done" | "error";

export function framePoses(f: FrameMessage): TimedPoses {
  return { t: f.t, poses: f.bodies as unknown as Record<string, Record<string, Pose>> };
}

export function useSimulation() {
  const ws = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<SimStatus>("idle");
  const [scene, setScene] = useState<SceneMessage | null>(null);
  const [frame, setFrame] = useState<FrameMessage | null>(null);
  const [result, setResult] = useState<ResultMessage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<FrameMessage["events"]>([]);
  const [trail, setTrail] = useState<[number, number][]>([]);
  const history = useRef<TimedPoses[]>([]);

  const close = useCallback(() => {
    ws.current?.close();
    ws.current = null;
  }, []);

  const start = useCallback(
    (req: SimStart) => {
      close();
      setStatus("connecting");
      setScene(null);
      setFrame(null);
      setResult(null);
      setError(null);
      setEvents([]);
      setTrail([]);
      history.current = [];
      const socket = new WebSocket(simSocketUrl());
      ws.current = socket;
      socket.onopen = () => socket.send(JSON.stringify(req));
      socket.onmessage = (ev: MessageEvent<string>) => {
        const msg = JSON.parse(ev.data) as ServerMessage;
        if (msg.type === "scene") {
          setScene(msg);
          setStatus("running");
        } else if (msg.type === "frame") {
          setFrame(msg);
          history.current = [...history.current.slice(-1), framePoses(msg)];
          if (msg.events.length) setEvents((e) => [...e, ...msg.events].slice(-200));
          const chassis = msg.bodies["ego"]?.["chassis"];
          if (chassis) setTrail((tr) => [...tr, [chassis[0][0], chassis[0][1]] as [number, number]].slice(-4000));
        } else if (msg.type === "result") {
          setResult(msg);
          setStatus("done");
        } else {
          setError(msg.message);
          setStatus("error");
        }
      };
      socket.onerror = () => {
        setError("WebSocket error — is the engine running?");
        setStatus("error");
      };
    },
    [close],
  );

  const send = useCallback((msg: object) => ws.current?.send(JSON.stringify(msg)), []);
  const pause = useCallback(() => {
    send({ type: "pause" });
    setStatus("paused");
  }, [send]);
  const resume = useCallback(() => {
    send({ type: "resume" });
    setStatus("running");
  }, [send]);
  const stop = useCallback(() => send({ type: "stop" }), [send]);
  const setSpeed = useCallback((speed: number) => send({ type: "speed", speed }), [send]);

  useEffect(() => close, [close]);
  return { status, scene, frame, result, error, events, trail, history, start, pause, resume, stop, setSpeed, send };
}
