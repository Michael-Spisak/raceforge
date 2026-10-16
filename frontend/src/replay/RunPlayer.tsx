import { Fragment, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, runs, type Schemas } from "../api/client";
import { Dashboard } from "../live/Dashboard";
import type { CarFrame } from "../live/useCarLink";
import { frameAt, speedSeries } from "./timeline";

type Timeline = Schemas["RunTimeline"];
const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));
const SPEEDS = [1, 2, 5] as const;

/** Replay of team drives (spec 0034): scrubber, play/pause, dashboard at the cursor, comparison. */
export function RunPlayer() {
  const { t } = useTranslation();
  const [list, setList] = useState<Schemas["RunInfo"][]>([]);
  const [run, setRun] = useState<Timeline | null>(null);
  const [other, setOther] = useState<Timeline | null>(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1);
  const [seek, setSeek] = useState(0); // bumps on jumps so the dashboard's history restarts
  const [diff, setDiff] = useState<Record<string, number> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => void runs.list().then((r) => setList(r.filter((x) => x.drive))).catch((e: unknown) => setError(message(e))), []);
  useEffect(() => {
    if (!playing || !run) return;
    let last = performance.now();
    const id = window.setInterval(() => {
      const now = performance.now();
      setCursor((c) => {
        const next = c + ((now - last) / 1000) * speed;
        if (next >= run.duration_s) setPlaying(false);
        return Math.min(next, run.duration_s);
      });
      last = now;
    }, 50);
    return () => window.clearInterval(id);
  }, [playing, run, speed]);

  const open = (slug: string, second = false) => {
    setError("");
    runs.timeline(slug).then((tl) => {
      if (second) {
        setOther(tl);
        if (run) void runs.compare(run.run, tl.run).then((c) => setDiff(c.b_minus_a)).catch(() => setDiff(null));
      } else {
        setRun(tl);
        setOther(null);
        setDiff(null);
        setCursor(0);
        setSeek((n) => n + 1);
      }
    }).catch((e: unknown) => setError(message(e)));
  };
  const jump = (to: number) => {
    setCursor(Math.max(0, Math.min(to, run?.duration_s ?? 0)));
    setSeek((n) => n + 1);
  };

  const frame = run ? (frameAt(run.t, run.frames, cursor) as CarFrame | null) : null;
  const marks = useMemo(
    () => (run ? [...run.notes.map((n) => ({ t: Number(n.t), text: `📝 ${String(n.text)}` })), ...run.events.map((e) => ({ t: Number(e.t), text: `${String(e.kind)} ${String(e.detail)}` }))].sort((a, b) => a.t - b.t) : []),
    [run],
  );

  return (
    <div data-testid="run-player">
      <div className="panel">
        <h4 style={{ marginTop: 0 }}>{t("runs.title")}</h4>
        {list.length === 0 && <p className="muted">{t("runs.none")}</p>}
        <ul className="list">
          {list.map((r) => (
            <li key={r.run}>
              <button type="button" onClick={() => open(r.run)}>{r.run}</button>{" "}
              <span className="muted">{r.message}</span>{" "}
              {run && run.run !== r.run && <button type="button" onClick={() => open(r.run, true)}>{t("runs.compare")}</button>}
            </li>
          ))}
        </ul>
        {error && <p className="error">{error}</p>}
      </div>
      {run && (
        <>
          <div className="panel">
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <button type="button" className="primary" onClick={() => { if (cursor >= run.duration_s) jump(0); setPlaying(!playing); }}>
                {playing ? t("runs.pause") : t("runs.play")}
              </button>
              {SPEEDS.map((s) => (
                <button key={s} type="button" aria-pressed={speed === s} onClick={() => setSpeed(s)}>{s}×</button>
              ))}
              <span style={{ fontVariantNumeric: "tabular-nums" }}>{cursor.toFixed(1)} / {run.duration_s.toFixed(1)} s</span>
              <span className="muted">{run.car}</span>
            </div>
            <input type="range" min={0} max={run.duration_s} step={0.05} value={cursor} style={{ width: "100%" }}
                   aria-label={t("runs.timeline")} data-testid="run-scrubber" onChange={(e) => jump(Number(e.target.value))} />
            <div style={{ position: "relative", height: 10 }}>
              {marks.map((m, i) => (
                <span key={i} title={m.text} onClick={() => jump(m.t)}
                      style={{ position: "absolute", left: `${(m.t / Math.max(run.duration_s, 0.001)) * 100}%`, width: 3, height: 10, background: m.text.startsWith("📝") ? "var(--c2)" : "var(--c1)", cursor: "pointer" }} />
              ))}
            </div>
            <ul className="list" style={{ maxHeight: 120, overflow: "auto" }}>
              {marks.map((m, i) => <li key={i}><button type="button" onClick={() => jump(m.t)}>{m.t.toFixed(1)} s</button> {m.text}</li>)}
            </ul>
          </div>
          {other && <Comparison a={run} b={other} diff={diff} />}
          <Dashboard key={seek} frame={frame} lastFrameAt={Math.round(cursor * 1000)} rtt={null} silent />
        </>
      )}
    </div>
  );
}

function Comparison({ a, b, diff }: { a: Timeline; b: Timeline; diff: Record<string, number> | null }) {
  const { t } = useTranslation();
  const W = 600, H = 140;
  const sa = speedSeries(a), sb = speedSeries(b);
  const span = Math.max(sa.at(-1)?.[0] ?? 1, sb.at(-1)?.[0] ?? 1, 1);
  const top = Math.max(...sa.map((p) => p[1]), ...sb.map((p) => p[1]), 0.1);
  const path = (pts: [number, number][]) => pts.map(([x, y]) => `${((x / span) * W).toFixed(1)},${(H - (y / top) * (H - 6)).toFixed(1)}`).join(" ");
  return (
    <div className="panel" data-testid="run-compare">
      <strong>{t("runs.compare_title", { a: a.run, b: b.run })}</strong>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label={t("runs.compare_title", { a: a.run, b: b.run })}>
        <polyline points={path(sa)} fill="none" stroke="var(--c1)" strokeWidth={2} />
        <polyline points={path(sb)} fill="none" stroke="var(--c2)" strokeWidth={2} />
      </svg>
      <div className="muted" style={{ fontSize: 11 }}>
        <span style={{ color: "var(--c1)" }}>━</span> {a.run} · <span style={{ color: "var(--c2)" }}>━</span> {b.run} · {t("runs.synced")}
      </div>
      {diff && (
        <dl className="kv">
          {Object.entries(diff).map(([k, v]) => (
            <Fragment key={k}><dt>{k}</dt><dd>{v > 0 ? "+" : ""}{v}</dd></Fragment>
          ))}
        </dl>
      )}
    </div>
  );
}
