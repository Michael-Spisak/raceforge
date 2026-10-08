import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { ApiError, api, type ReplaySummary } from "../api/client";

function Plot({ title, t, series }: { title: string; t: number[]; series: { label: string; values: (number | null)[]; color: string }[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const plot = new uPlot(
      {
        title, width: ref.current.clientWidth || 600, height: 180,
        series: [{}, ...series.map((s) => ({ label: s.label, stroke: s.color, width: 1.5 }))],
        axes: [{ stroke: "currentColor" }, { stroke: "currentColor" }],
      },
      [t, ...series.map((s) => s.values)] as uPlot.AlignedData,
      ref.current,
    );
    return () => plot.destroy();
  }, [title, t, series]);
  return <div ref={ref} className="plot" />;
}

function Trajectory({ points }: { points: [number, number][] }) {
  if (points.length < 2) return null;
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const span = Math.max(maxX - minX, maxY - minY, 0.1);
  const d = points.map(([x, y], i) => `${i ? "L" : "M"}${(((x - minX) / span) * 380 + 10).toFixed(1)},${(390 - ((y - minY) / span) * 380).toFixed(1)}`).join(" ");
  return (
    <svg viewBox="0 0 400 400" width="100%" style={{ maxWidth: 400 }} data-testid="trajectory">
      <path d={d} fill="none" stroke="var(--c1)" strokeWidth={2} />
    </svg>
  );
}

export function ReplayScreen({ initialPath = "" }: { initialPath?: string }) {
  const { t } = useTranslation();
  const [path, setPath] = useState(initialPath);
  const [data, setData] = useState<ReplaySummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const open = () => {
    setError(null);
    api.replay(path).then(setData).catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e)));
  };

  return (
    <div className="screen">
      <aside className="side">
        <div className="field">
          <label htmlFor="path">{t("replay.path")}</label>
          <input id="path" data-testid="replay-path" value={path} onChange={(e) => setPath(e.target.value)} />
        </div>
        <button className="primary" data-testid="replay-open" onClick={open} disabled={!path}>{t("replay.open")}</button>
        {error && <p className="error">{error}</p>}
        {data && (
          <dl className="kv" style={{ marginTop: 12 }} data-testid="replay-summary">
            <dt>{t("replay.frames")}</dt><dd>{data.frames}</dd>
            <dt>{t("replay.duration")}</dt><dd>{data.duration_s.toFixed(1)} s</dd>
          </dl>
        )}
        {data && <><h4>{t("replay.trajectory")}</h4><Trajectory points={data.truth_xy} /></>}
      </aside>
      <section className="main" style={{ overflow: "auto", display: "block" }}>
        {data && (
          <>
            <Plot title={t("replay.steering")} t={data.t} series={[{ label: "cmd", values: data.steering_cmd, color: "#0072b2" }]} />
            <Plot title={t("replay.speed")} t={data.t} series={[{ label: "cmd", values: data.speed_cmd, color: "#e69f00" }, { label: "meas", values: data.speed_meas, color: "#009e73" }]} />
          </>
        )}
      </section>
    </div>
  );
}
