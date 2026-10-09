import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type QuickTrack, type QuickTrackInfo } from "../api/client";
import { type Alarm, Rolling, type Thresholds, alarms, beep, loadThresholds, saveThresholds } from "./dashboard";
import type { CarEvent, CarFrame } from "./useCarLink";

const PLOT_SPAN_MS = 30_000;
const STATE_SPAN_MS = 60_000;
const TRAIL = 600;
const DEG = 180 / Math.PI;

interface Props {
  frame: CarFrame | null;
  lastFrameAt: number;
  rtt: number | null;
  onEvent?: (e: CarEvent) => void;
}

/** Pose from the frame, else from the controller's pose.* channels (spec 0029). */
function poseOf(f: CarFrame | null): { pose: { x: number; y: number; yaw: number }; confidence: number } | null {
  if (f?.pose_est) return f.pose_est;
  const c = f?.channels;
  const [x, y, yaw] = [c?.["pose.x"], c?.["pose.y"], c?.["pose.yaw"]];
  if (typeof x !== "number" || typeof y !== "number" || typeof yaw !== "number") return null;
  const conf = c?.["pose.conf"];
  return { pose: { x, y, yaw }, confidence: typeof conf === "number" ? conf : 1 };
}

/** Live dashboard (spec 0027): health, plots, state timeline, top-down view, timer, alarms. */
export function Dashboard({ frame, lastFrameAt, rtt, onEvent }: Props) {
  const { t } = useTranslation();
  const plots = useRef(new Rolling(STATE_SPAN_MS));
  const trail = useRef<[number, number][]>([]);
  const movedAt = useRef<number | null>(null);
  const prevFaults = useRef<string[]>([]);
  const [, setTick] = useState(0);
  const [thresholds, setThresholds] = useState<Thresholds>(loadThresholds);
  const [active, setActive] = useState<Alarm[]>([]);
  const [muted, setMuted] = useState(false);
  const [editing, setEditing] = useState(false);
  const [tracks, setTracks] = useState<QuickTrackInfo[]>([]);
  const [track, setTrack] = useState<QuickTrack | null>(null);

  useEffect(() => void api.quickTracks().then((ts) => setTracks(ts.filter((x) => !x.error))).catch(() => undefined), []);

  // every frame: history, trail, timer, alarms (rendering is throttled below)
  useEffect(() => {
    if (!frame) return;
    plots.current.push(frame, lastFrameAt);
    const p = poseOf(frame)?.pose;
    if (p) trail.current = [...trail.current.slice(-TRAIL), [p.x, p.y]];
    if (movedAt.current == null && Math.abs(frame.meas?.speed_m_s ?? 0) > 0.05) movedAt.current = lastFrameAt;
    const raised = alarms(frame, rtt, thresholds, prevFaults.current);
    prevFaults.current = frame.faults ?? [];
    const fresh = raised.filter((a) => a.key === "fault" || !active.some((x) => x.key === a.key));
    if (fresh.length) {
      if (!muted) beep();
      for (const a of fresh) onEvent?.({ at: Date.now(), kind: t("dash.alarm"), detail: `${t(`dash.alarm_${a.key}`)}: ${a.value}` });
    }
    const keys = raised.map((a) => a.key).join();
    if (keys !== active.map((a) => a.key).join()) setActive(raised);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastFrameAt]);
  useEffect(() => {
    const id = window.setInterval(() => setTick((n) => n + 1), 100); // plots at ≤ 10 Hz
    return () => window.clearInterval(id);
  }, []);

  const f = frame;
  const now = lastFrameAt || Date.now();
  const rate = f?.loop?.rate_hz;
  const battery = f?.power?.ev3_battery_v ?? f?.power?.motor_battery_v;
  const cpu = f?.power?.board_cpu_temp_c;
  const lights: [string, "ok" | "warn" | "bad" | "none", string][] = [
    ["link", rtt == null ? "none" : rtt > thresholds.maxRttMs ? "warn" : "ok", rtt == null ? "–" : `${rtt.toFixed(0)} ms`],
    ["loop", rate == null ? "none" : rate < thresholds.minLoopHz ? "bad" : "ok", rate == null ? "–" : `${rate.toFixed(0)} Hz · ${f?.loop?.deadline_misses ?? 0}`],
    ["battery", battery == null ? "none" : battery < thresholds.minBatteryV ? "bad" : "ok", battery == null ? "–" : `${battery.toFixed(1)} V`],
    ["cpu", cpu == null ? "none" : cpu > thresholds.maxCpuC ? "warn" : "ok", cpu == null ? "–" : `${cpu.toFixed(0)} °C${f?.power?.board_cpu_load != null ? ` · ${(f.power.board_cpu_load * 100).toFixed(0)} %` : ""}`],
    ["faults", f?.faults?.length ? "bad" : f ? "ok" : "none", f?.faults?.length ? f.faults.join(", ") : "–"],
  ];
  const lap = f?.channels?.lap;
  const lapTime = f?.channels?.lap_time_s;
  const driven = movedAt.current != null ? (now - movedAt.current) / 1000 : null;

  return (
    <div data-testid="dashboard">
      {active.length > 0 && (
        <div className="panel error" role="alert" data-testid="dash-alarm">
          {active.map((a) => `${t(`dash.alarm_${a.key}`)}: ${a.value}`).join(" · ")}
        </div>
      )}
      <div className="panel">
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
          {lights.map(([key, level, value]) => (
            <span key={key} data-testid={`dash-light-${key}`} title={t(`dash.${key}`)}>
              <span style={{ color: { ok: "#2a9d55", warn: "#d99a00", bad: "#d33", none: "#888" }[level] }}>●</span>{" "}
              {t(`dash.${key}`)} <span className="muted">{value}</span>
            </span>
          ))}
          <span style={{ marginLeft: "auto" }}>
            {t("dash.driving")} {driven == null ? "–" : `${driven.toFixed(0)} s`}
            {lap != null && ` · ${t("dash.lap")} ${String(lap)}${typeof lapTime === "number" ? ` (${lapTime.toFixed(2)} s)` : ""}`}
          </span>
          <label><input type="checkbox" checked={muted} onChange={(e) => setMuted(e.target.checked)} /> {t("dash.mute")}</label>
          <button type="button" onClick={() => setEditing(!editing)}>{t("dash.thresholds")}</button>
        </div>
        {editing && <ThresholdEditor value={thresholds} onChange={(v) => { setThresholds(v); saveThresholds(v); }} />}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 8 }}>
        <Plot title={t("dash.speed")} unit="m/s" now={now} lines={[
          [plots.current.series((x) => x.cmd?.speed_m_s), "#888", t("dash.commanded")],
          [plots.current.series((x) => x.meas?.speed_m_s), "#2a6fd3", t("dash.measured")],
        ]} />
        <Plot title={t("dash.steering")} unit="°" now={now} lines={[
          [plots.current.series((x) => (x.cmd ? x.cmd.steering_rad * DEG : null)), "#888", t("dash.commanded")],
          [plots.current.series((x) => (x.meas?.steering_rad != null ? x.meas.steering_rad * DEG : null)), "#2a6fd3", t("dash.measured")],
        ]} />
        <Plot title={t("dash.battery")} unit="V" now={now} lines={[[plots.current.series((x) => x.power?.ev3_battery_v ?? x.power?.motor_battery_v), "#d99a00", ""]]} />
        <Plot title={t("dash.loop")} unit="Hz" now={now} lines={[[plots.current.series((x) => x.loop?.rate_hz), "#2a9d55", ""]]} />
      </div>
      <StateTimeline spans={plots.current.states()} now={now} />
      <div className="panel">
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <h4 style={{ margin: 0 }}>{t("dash.topdown")}</h4>
          <select aria-label={t("dash.track")} value={track?.name ?? ""} onChange={(e) => {
            const name = e.target.value;
            if (!name) setTrack(null);
            else void api.quickTrack(name).then(setTrack).catch(() => setTrack(null));
            trail.current = [];
          }}>
            <option value="">{t("dash.no_track")}</option>
            {tracks.map((q) => <option key={q.name} value={q.name}>{q.name}</option>)}
          </select>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
          <Radar frame={f} />
          {poseOf(f) && <TrackMap track={track} trail={trail.current} pose={poseOf(f)!.pose} confidence={poseOf(f)!.confidence} />}
        </div>
      </div>
    </div>
  );
}

function ThresholdEditor({ value, onChange }: { value: Thresholds; onChange: (t: Thresholds) => void }) {
  const { t } = useTranslation();
  const field = (key: keyof Thresholds, step: number) => (
    <label key={key} style={{ marginRight: 12 }}>
      {t(`dash.th_${key}`)}{" "}
      <input type="number" step={step} style={{ width: 70 }} value={value[key]} onChange={(e) => onChange({ ...value, [key]: Number(e.target.value) })} />
    </label>
  );
  return <div style={{ marginTop: 8 }}>{field("minLoopHz", 1)}{field("minBatteryV", 0.1)}{field("maxCpuC", 1)}{field("maxRttMs", 10)}</div>;
}

type Line = [[number, number][], string, string];

function Plot({ title, unit, lines, now }: { title: string; unit: string; lines: Line[]; now: number }) {
  const W = 300, H = 90;
  const values = lines.flatMap(([pts]) => pts.filter(([at]) => at >= now - PLOT_SPAN_MS).map(([, v]) => v));
  const lo = values.length ? Math.min(...values) : 0;
  const hi = values.length ? Math.max(...values) : 1;
  const span = hi - lo || 1;
  const x = (at: number) => ((at - (now - PLOT_SPAN_MS)) / PLOT_SPAN_MS) * W;
  const y = (v: number) => H - 4 - ((v - lo) / span) * (H - 8);
  const last = lines[lines.length - 1][0].at(-1)?.[1];
  return (
    <div className="panel" style={{ margin: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <strong>{title}</strong>
        <span className="muted">{last == null ? "–" : `${last.toFixed(2)} ${unit}`}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label={title}>
        {lines.map(([pts, colour, label]) => (
          <polyline key={label || colour} fill="none" stroke={colour} strokeWidth={1.5}
                    points={pts.filter(([at]) => at >= now - PLOT_SPAN_MS).map(([at, v]) => `${x(at).toFixed(1)},${y(v).toFixed(1)}`).join(" ")} />
        ))}
        <text x={2} y={10} fontSize={9} fill="currentColor" opacity={0.6}>{hi.toFixed(1)}</text>
        <text x={2} y={H - 2} fontSize={9} fill="currentColor" opacity={0.6}>{lo.toFixed(1)}</text>
      </svg>
      {lines.length > 1 && (
        <div className="muted" style={{ fontSize: 11 }}>
          {lines.map(([, colour, label]) => <span key={label} style={{ marginRight: 8 }}><span style={{ color: colour }}>━</span> {label}</span>)}
        </div>
      )}
    </div>
  );
}

const STATE_COLOURS = ["#2a6fd3", "#2a9d55", "#d99a00", "#8a4fd3", "#d33", "#1aa3a3", "#c2569b"];
const colourOf = (state: string) => {
  let h = 0;
  for (const c of state) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return STATE_COLOURS[h % STATE_COLOURS.length];
};

function StateTimeline({ spans, now }: { spans: [string, number, number][]; now: number }) {
  const { t } = useTranslation();
  const W = 600;
  const x = (at: number) => Math.max(0, ((at - (now - STATE_SPAN_MS)) / STATE_SPAN_MS) * W);
  return (
    <div className="panel">
      <strong>{t("dash.states")}</strong>
      <svg viewBox={`0 0 ${W} 22`} width="100%" height={22} role="img" aria-label={t("dash.states")}>
        {spans.map(([state, from, to], i) => {
          const end = i + 1 < spans.length ? spans[i + 1][1] : now;
          return (
            <g key={`${state}-${from}`}>
              <title>{state}</title>
              <rect x={x(from)} y={2} width={Math.max(1, x(Math.max(to, end)) - x(from))} height={18} fill={colourOf(state)} />
            </g>
          );
        })}
      </svg>
      <div className="muted" style={{ fontSize: 11 }}>
        {[...new Set(spans.map(([s]) => s))].map((s) => <span key={s} style={{ marginRight: 8 }}><span style={{ color: colourOf(s) }}>■</span> {s}</span>)}
      </div>
    </div>
  );
}

/** Car-centric view: LiDAR points and range sensors (forward = up). */
function Radar({ frame }: { frame: CarFrame | null }) {
  const { t } = useTranslation();
  const S = 220, R = 3.0; // metres to the edge
  const sensors = Object.entries(frame?.meas?.sensors ?? {});
  const pts: [number, number][] = [];
  for (const [, s] of sensors) {
    if (s.kind !== "range_array") continue;
    const arr = s as { angle_min_rad: number; angle_increment_rad: number; ranges: (number | null)[] };
    arr.ranges.forEach((r, i) => {
      if (r == null || r > R) return;
      const a = arr.angle_min_rad + i * arr.angle_increment_rad;
      pts.push([S / 2 - Math.sin(a) * (r / R) * (S / 2), S / 2 - Math.cos(a) * (r / R) * (S / 2)]);
    });
  }
  const ranges = sensors.filter(([, s]) => s.kind === "range") as [string, { kind: "range"; distance_m: number | null }][];
  return (
    <div>
      <svg viewBox={`0 0 ${S} ${S}`} width={S} height={S} role="img" aria-label={t("dash.topdown")} style={{ background: "var(--panel-2, #0001)", borderRadius: 4 }}>
        {[1, 2, 3].map((m) => <circle key={m} cx={S / 2} cy={S / 2} r={(m / R) * (S / 2)} fill="none" stroke="currentColor" opacity={0.15} />)}
        {pts.map(([px, py], i) => <circle key={i} cx={px} cy={py} r={1.5} fill="#d33" />)}
        <polygon points={`${S / 2},${S / 2 - 9} ${S / 2 - 5},${S / 2 + 6} ${S / 2 + 5},${S / 2 + 6}`} fill="#2a6fd3" />
      </svg>
      {ranges.length > 0 && (
        <ul className="list" style={{ maxWidth: S }}>
          {ranges.map(([id, s]) => (
            <li key={id}>
              {id}: {s.distance_m == null ? "–" : `${s.distance_m.toFixed(2)} m`}
              <div style={{ background: "#2a6fd3", height: 4, width: `${Math.min(100, ((s.distance_m ?? 0) / R) * 100)}%` }} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** World view: quick track (centreline + corridor) with the car's pose and trail. */
function TrackMap({ track, trail, pose, confidence }: { track: QuickTrack | null; trail: [number, number][]; pose: { x: number; y: number; yaw: number }; confidence: number }) {
  const { t } = useTranslation();
  const S = 320;
  const pts = [...(track?.points ?? []), ...trail, [pose.x, pose.y] as [number, number]];
  const pad = (track?.width_m ?? 1) + 0.5;
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const minX = Math.min(...xs) - pad, maxX = Math.max(...xs) + pad, minY = Math.min(...ys) - pad, maxY = Math.max(...ys) + pad;
  const scale = S / Math.max(maxX - minX, maxY - minY, 1);
  const px = (x: number) => (x - minX) * scale;
  const py = (y: number) => S - (y - minY) * scale;
  const centre = track ? [...track.points, ...(track.loop ? [track.points[0]] : [])] : [];
  const path = (p: [number, number][]) => p.map(([x, y]) => `${px(x).toFixed(1)},${py(y).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${S} ${S}`} width={S} height={S} role="img" aria-label={t("dash.map")} style={{ background: "var(--panel-2, #0001)", borderRadius: 4 }}>
      {track && <polyline points={path(centre)} fill="none" stroke="currentColor" opacity={0.15} strokeWidth={track.width_m * scale} strokeLinejoin="round" />}
      {track && <polyline points={path(centre)} fill="none" stroke="currentColor" opacity={0.4} strokeDasharray="4 4" />}
      <polyline points={path(trail)} fill="none" stroke="#2a6fd3" strokeWidth={1.5} />
      <g transform={`translate(${px(pose.x)} ${py(pose.y)}) rotate(${(-pose.yaw * DEG + 90).toFixed(1)})`}>
        <polygon points="0,-8 -5,6 5,6" fill="#2a6fd3" opacity={0.4 + 0.6 * confidence} />
      </g>
      <text x={4} y={S - 4} fontSize={10} fill="currentColor" opacity={0.7}>{t("dash.confidence", { pct: (confidence * 100).toFixed(0) })}</text>
    </svg>
  );
}
