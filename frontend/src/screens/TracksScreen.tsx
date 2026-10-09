import { type MouseEvent as ReactMouseEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, type QuickObstacle, type QuickTrack, type QuickTrackInfo, type QuickTrackPreview } from "../api/client";

type Pt = [number, number];
type Mode = "point" | QuickObstacle["kind"];
interface Draft {
  name: string;
  points: Pt[];
  width_m: number;
  loop: boolean;
  laps: number;
  corner_radius_m: number | null;
  obstacles: QuickObstacle[];
}

const EMPTY: Draft = { name: "", points: [], width_m: 1.6, loop: true, laps: 3, corner_radius_m: null, obstacles: [] };
const SNAP = 0.25;
const OBSTACLE_SIZE: Record<QuickObstacle["kind"], [number, number]> = { bin: [0.35, 0.35], pillar: [0.3, 0.3], bench: [1.2, 0.4] };
const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

function toTrack(d: Draft): QuickTrack {
  return {
    name: d.name || "quick track",
    points: d.points,
    width_m: d.width_m,
    loop: d.loop,
    laps: d.laps,
    corner_radius_m: d.corner_radius_m,
    obstacles: d.obstacles,
    wall_height_m: 2.5,
    friction: 0.75,
  };
}

/** Draw a corridor centreline in 2D; the engine builds walls, checkpoints and start grid (spec 0014). */
export function TracksScreen() {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [history, setHistory] = useState<Draft[]>([]);
  const [mode, setMode] = useState<Mode>("point");
  const [snap, setSnap] = useState(true);
  const [preview, setPreview] = useState<QuickTrackPreview | null>(null);
  const [saved, setSaved] = useState<QuickTrackInfo[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const svg = useRef<SVGSVGElement | null>(null);
  const drag = useRef<{ index: number; moved: boolean } | null>(null);

  const loadList = useCallback(() => api.quickTracks().then(setSaved).catch(() => undefined), []);
  useEffect(() => void loadList(), [loadList]);

  const change = (next: Draft, record = true) => {
    if (record) setHistory((h) => [...h.slice(-49), draft]);
    setDraft(next);
    setNote("");
  };

  // Engine preview, debounced while drawing.
  useEffect(() => {
    if (draft.points.length < (draft.loop ? 3 : 2)) {
      setPreview(null);
      return;
    }
    const id = window.setTimeout(() => {
      api.quickTrackPreview(toTrack(draft)).then(setPreview).catch((e: unknown) => setPreview({ ok: false, error: message(e), length_m: 0, centreline: [], walls: [], objects: [] }));
    }, 250);
    return () => window.clearTimeout(id);
  }, [draft]);

  // View box in metres (y up in the world, so SVG y = -y); grows with the drawing.
  const view = useMemo(() => {
    const xs = [-1, 31, ...draft.points.map((p) => p[0]), ...draft.obstacles.map((o) => o.x)];
    const ys = [-1, 21, ...draft.points.map((p) => p[1]), ...draft.obstacles.map((o) => o.y)];
    for (const w of preview?.walls ?? []) for (const [x, y] of w) { xs.push(x); ys.push(y); }
    const m = 2;
    const x0 = Math.floor(Math.min(...xs) - m), x1 = Math.ceil(Math.max(...xs) + m);
    const y0 = Math.floor(Math.min(...ys) - m), y1 = Math.ceil(Math.max(...ys) + m);
    return { x0, x1, y0, y1 };
  }, [draft, preview]);

  const world = (e: { clientX: number; clientY: number }): Pt | null => {
    const el = svg.current;
    const ctm = el?.getScreenCTM();
    if (!el || !ctm) return null;
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(ctm.inverse());
    const q = (v: number) => (snap ? Math.round(v / SNAP) * SNAP : Math.round(v * 100) / 100);
    return [q(p.x), q(-p.y)];
  };

  const onBackgroundClick = (e: ReactMouseEvent<SVGSVGElement>) => {
    if (drag.current?.moved) return;
    const p = world(e);
    if (!p) return;
    if (mode === "point") change({ ...draft, points: [...draft.points, p] });
    else change({ ...draft, obstacles: [...draft.obstacles, { kind: mode, x: p[0], y: p[1], yaw_deg: 0 }] });
  };
  const onMove = (e: ReactMouseEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d || e.buttons !== 1) return;
    const p = world(e);
    if (!p) return;
    if (!d.moved) setHistory((h) => [...h.slice(-49), draft]);
    d.moved = true;
    setDraft((cur) => ({ ...cur, points: cur.points.map((q, i) => (i === d.index ? p : q)) }));
  };
  const endDrag = () => {
    window.setTimeout(() => { drag.current = null; }, 0); // after the click handler has seen `moved`
  };

  const undo = () => {
    const prev = history[history.length - 1];
    if (!prev) return;
    setHistory((h) => h.slice(0, -1));
    setDraft(prev);
  };

  const save = () => {
    setError("");
    const name = draft.name.trim();
    if (!name) {
      setError(t("tracks.name"));
      return;
    }
    api.saveQuickTrack(name, toTrack(draft))
      .then((pv) => { setPreview(pv); setNote(pv.ok ? t("tracks.saved_ok") : ""); return loadList(); })
      .catch((e: unknown) => setError(message(e)));
  };
  const load = (name: string) => {
    api.quickTrack(name).then((q) => {
      setHistory([]);
      setDraft({
        name: q.name, points: q.points as Pt[], width_m: q.width_m ?? 1.6, loop: q.loop ?? true, laps: q.laps ?? 3,
        corner_radius_m: q.corner_radius_m ?? null, obstacles: q.obstacles ?? [],
      });
    }).catch((e: unknown) => setError(message(e)));
  };
  const remove = (name: string) => api.deleteQuickTrack(name).then(loadList).catch((e: unknown) => setError(message(e)));

  const poly = (pts: Pt[] | [number, number][]) => pts.map(([x, y]) => `${x},${-y}`).join(" ");
  const grid: [number, number, number, number][] = [];
  for (let x = view.x0; x <= view.x1; x++) grid.push([x, view.y0, x, view.y1]);
  for (let y = view.y0; y <= view.y1; y++) grid.push([view.x0, y, view.x1, y]);

  return (
    <div className="screen">
      <aside className="side">
        <div className="panel">
          <div className="field">
            <label htmlFor="qt-name">{t("tracks.name")}</label>
            <input id="qt-name" data-testid="qt-name" value={draft.name} onChange={(e) => change({ ...draft, name: e.target.value }, false)} />
          </div>
          <div className="field">
            <label htmlFor="qt-width">{t("tracks.width")}</label>
            <input id="qt-width" type="number" min={0.6} max={4} step={0.1} value={draft.width_m}
                   onChange={(e) => change({ ...draft, width_m: Number(e.target.value) })} />
          </div>
          <div className="field"><label><input type="checkbox" checked={draft.loop} onChange={(e) => change({ ...draft, loop: e.target.checked })} /> {t("tracks.loop")}</label></div>
          {draft.loop && (
            <div className="field">
              <label htmlFor="qt-laps">{t("tracks.laps")}</label>
              <input id="qt-laps" type="number" min={1} max={20} value={draft.laps} onChange={(e) => change({ ...draft, laps: Number(e.target.value) })} />
            </div>
          )}
          <div className="field">
            <label htmlFor="qt-radius">{t("tracks.radius")}</label>
            <input id="qt-radius" type="number" min={0.1} max={10} step={0.1} value={draft.corner_radius_m ?? ""}
                   onChange={(e) => change({ ...draft, corner_radius_m: e.target.value === "" ? null : Number(e.target.value) })} />
          </div>
          <div className="field">
            <label htmlFor="qt-mode">{t("tracks.mode")}</label>
            <select id="qt-mode" value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
              {(["point", "bin", "pillar", "bench"] as const).map((m) => <option key={m} value={m}>{t(`tracks.mode_${m}`)}</option>)}
            </select>
          </div>
          <div className="field"><label><input type="checkbox" checked={snap} onChange={(e) => setSnap(e.target.checked)} /> {t("tracks.snap")}</label></div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <button type="button" className="primary" onClick={save} data-testid="qt-save" disabled={draft.points.length < 2}>{t("tracks.save")}</button>
            <button type="button" onClick={undo} disabled={!history.length}>{t("tracks.undo")}</button>
            <button type="button" onClick={() => change({ ...draft, points: [], obstacles: [] })}>{t("tracks.clear")}</button>
            <button type="button" onClick={() => { setHistory([]); setDraft(EMPTY); }}>{t("tracks.new")}</button>
          </div>
          {preview?.ok && <p data-testid="qt-length">{t("tracks.length", { m: preview.length_m.toFixed(1) })}</p>}
          {preview && !preview.ok && <p className="warning" data-testid="qt-invalid">{t("tracks.invalid", { error: preview.error ?? "" })}</p>}
          {note && <p>{note}</p>}
          {error && <p className="error" role="alert">{error}</p>}
          <p className="muted">{t("tracks.help")}</p>
        </div>
        <div className="panel">
          <h4 style={{ marginTop: 0 }}>{t("tracks.saved")}</h4>
          {saved.length === 0 && <p className="muted">{t("tracks.none")}</p>}
          <ul className="list" data-testid="qt-list">
            {saved.map((s) => (
              <li key={s.name} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <span style={{ flex: 1 }}>{s.name}{s.length_m ? ` · ${s.length_m.toFixed(0)} m` : ""}{s.error ? " ⚠" : ""}</span>
                <button type="button" onClick={() => load(s.name)}>{t("tracks.load")}</button>
                <button type="button" onClick={() => void remove(s.name)}>{t("tracks.delete")}</button>
              </li>
            ))}
          </ul>
        </div>
      </aside>
      <section className="main" style={{ display: "block", overflow: "hidden" }}>
        <svg ref={svg} data-testid="qt-canvas" style={{ width: "100%", height: "100%", background: "var(--bg)", cursor: "crosshair", userSelect: "none" }}
             viewBox={`${view.x0} ${-view.y1} ${view.x1 - view.x0} ${view.y1 - view.y0}`} preserveAspectRatio="xMidYMid meet"
             onClick={onBackgroundClick} onMouseMove={onMove} onMouseUp={endDrag} onMouseLeave={endDrag}
             onContextMenu={(e) => e.preventDefault()}>
          <g stroke="var(--border)" strokeWidth={1} vectorEffect="non-scaling-stroke">
            {grid.map(([a, b, c, d], i) => <line key={i} x1={a} y1={-b} x2={c} y2={-d} vectorEffect="non-scaling-stroke" strokeOpacity={(a === c ? a : b) % 5 === 0 ? 0.9 : 0.35} />)}
          </g>
          {preview?.ok && (
            <g>
              {preview.walls.map((w, i) => (
                <polyline key={i} points={poly(w)} fill="none" stroke="var(--text)" strokeWidth={3} vectorEffect="non-scaling-stroke" />
              ))}
              <polyline points={poly(preview.centreline)} fill="none" stroke="var(--muted)" strokeDasharray="4 4" strokeWidth={1} vectorEffect="non-scaling-stroke" />
              {preview.start_line && (
                <line x1={preview.start_line[0][0]} y1={-preview.start_line[0][1]} x2={preview.start_line[1][0]} y2={-preview.start_line[1][1]}
                      stroke="var(--accent)" strokeWidth={4} vectorEffect="non-scaling-stroke" />
              )}
            </g>
          )}
          <polyline points={poly(draft.loop && draft.points.length > 2 ? [...draft.points, draft.points[0] as Pt] : draft.points)}
                    fill="none" stroke="var(--accent)" strokeWidth={1.5} strokeOpacity={0.7} vectorEffect="non-scaling-stroke" />
          {draft.obstacles.map((o, i) => {
            const [w, d] = OBSTACLE_SIZE[o.kind];
            return (
              <rect key={`o${i}`} x={o.x - w / 2} y={-o.y - d / 2} width={w} height={d} fill="var(--warning, orange)" fillOpacity={0.8}
                    transform={`rotate(${-o.yaw_deg} ${o.x} ${-o.y})`}
                    onClick={(e) => e.stopPropagation()}
                    onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); change({ ...draft, obstacles: draft.obstacles.filter((_, j) => j !== i) }); }}>
                <title>{t(`tracks.mode_${o.kind}`)}</title>
              </rect>
            );
          })}
          {draft.points.map(([x, y], i) => (
            <circle key={`p${i}`} cx={x} cy={-y} r={0.18} fill={i === 0 ? "var(--accent)" : "var(--text)"} style={{ cursor: "grab" }}
                    data-testid={`qt-point-${i}`}
                    onMouseDown={(e) => { e.stopPropagation(); drag.current = { index: i, moved: false }; }}
                    onClick={(e) => e.stopPropagation()}
                    onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); change({ ...draft, points: draft.points.filter((_, j) => j !== i) }); }}>
              <title>{`${i + 1}: ${x.toFixed(2)}, ${y.toFixed(2)} m`}</title>
            </circle>
          ))}
        </svg>
      </section>
    </div>
  );
}
