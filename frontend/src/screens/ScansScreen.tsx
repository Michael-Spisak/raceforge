import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, type ScanDetail, type ScanMesh, type ScanTrack } from "../api/client";
import { CLASS_COLORS, PASS_COLORS, ScanMeshView, ScanTrajectory } from "../three/ScanModel";
import { Viewport } from "../three/Viewport";

const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));
const MB = 1_000_000;

interface Loaded {
  detail: ScanDetail;
  mesh: ScanMesh | null;
}

/** Union of the selected passes' bounding boxes → camera position and orbit target. */
export function framing(details: ScanDetail[]): { camera: [number, number, number]; target: [number, number, number] } {
  if (details.length === 0) return { camera: [6, -6, 5], target: [0, 0, 0] };
  const axis = (i: 0 | 1 | 2) => {
    const lo = Math.min(...details.map((d) => d.bounds[0][i]));
    const hi = Math.max(...details.map((d) => d.bounds[1][i]));
    return { mid: (lo + hi) / 2, span: hi - lo };
  };
  const [x, y, z] = [axis(0), axis(1), axis(2)];
  const size = Math.max(x.span, y.span, z.span, 1);
  return { camera: [x.mid + size * 0.7, y.mid - size * 0.9, z.mid + size * 0.8], target: [x.mid, y.mid, z.mid] };
}

export function ScansScreen() {
  const { t } = useTranslation();
  const [tracks, setTracks] = useState<ScanTrack[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loaded, setLoaded] = useState<Record<string, Loaded>>({});
  const [loading, setLoading] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [path, setPath] = useState("");
  const [showMesh, setShowMesh] = useState(true);
  const [showPath, setShowPath] = useState(true);
  const [colorBy, setColorBy] = useState<"class" | "pass">("class");
  const [hideCeiling, setHideCeiling] = useState(true);
  const [cut, setCut] = useState(false);
  const [cutHeight, setCutHeight] = useState(1.0); // metres above the lowest point

  const reload = useCallback(() => api.scans().then(setTracks).catch((e: unknown) => setError(message(e))), []);
  useEffect(() => void reload(), [reload]);

  const load = (sha: string) => {
    if (loaded[sha] || loading.includes(sha)) return;
    setLoading((l) => [...l, sha]);
    Promise.all([api.scan(sha), api.scanMesh(sha).catch(() => null)])
      .then(([detail, mesh]) => setLoaded((m) => ({ ...m, [sha]: { detail, mesh } })))
      .catch((e: unknown) => {
        setError(message(e));
        setSelected((s) => s.filter((x) => x !== sha));
      })
      .finally(() => setLoading((l) => l.filter((x) => x !== sha)));
  };

  const toggle = (sha: string) => {
    setError("");
    setSelected((s) => (s.includes(sha) ? s.filter((x) => x !== sha) : [...s, sha]));
    load(sha);
  };

  const open = (e: FormEvent) => {
    e.preventDefault();
    setError("");
    api.openScan(path)
      .then((ref) => { void reload(); setSelected((s) => [...new Set([...s, ref.sha256])]); load(ref.sha256); })
      .catch((err: unknown) => setError(message(err)));
  };

  const shown = selected.filter((sha) => loaded[sha]);
  const details = shown.flatMap((sha) => (loaded[sha] ? [loaded[sha].detail] : []));
  const frame = useMemo(() => framing(details), [shown.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  const floorZ = details.length ? Math.min(...details.map((d) => d.bounds[0][2])) : 0;
  const last = shown[shown.length - 1];
  const focus = last ? loaded[last] ?? null : null;

  return (
    <div className="screen">
      <aside className="side">
        <h3 style={{ marginTop: 0 }}>{t("scans.tracks")}</h3>
        <button onClick={() => void reload()} data-testid="scans-refresh">{t("scans.refresh")}</button>
        {tracks.length === 0 && <p className="muted">{t("scans.none")}</p>}
        {tracks.map((tr) => (
          <div key={`${tr.name}-${tr.version ?? ""}`} className="panel" data-testid={`scan-track-${tr.name}`}>
            <strong>{tr.name}</strong>{tr.version && <span className="muted"> · {tr.version}</span>}
            <ul className="list">
              {tr.passes.map((p) => (
                <li key={p.sha256}>
                  <label>
                    <input type="checkbox" checked={selected.includes(p.sha256)} onChange={() => toggle(p.sha256)}
                           data-testid={`scan-pass-${p.name}`} />{" "}
                    {p.name}
                  </label>
                  <span className="muted"> · {(p.size / MB).toFixed(0)} MB · {t(`scans.source_${p.source}`)}</span>
                  {loading.includes(p.sha256) && <span className="muted"> · {t("scans.loading")}</span>}
                </li>
              ))}
            </ul>
          </div>
        ))}
        <form onSubmit={open} className="field">
          <label htmlFor="scan-path">{t("scans.open_path")}</label>
          <div style={{ display: "flex", gap: 6 }}>
            <input id="scan-path" data-testid="scan-path" value={path} onChange={(e) => setPath(e.target.value)} />
            <button type="submit" disabled={!path} data-testid="scan-open">{t("scans.open")}</button>
          </div>
        </form>
        {error && <p className="error" role="alert">{error}</p>}

        <div className="panel">
          <label><input type="checkbox" checked={showMesh} onChange={(e) => setShowMesh(e.target.checked)} data-testid="scan-show-mesh" /> {t("scans.mesh")}</label>{" "}
          <label><input type="checkbox" checked={showPath} onChange={(e) => setShowPath(e.target.checked)} /> {t("scans.trajectory")}</label>
          <div><label><input type="checkbox" checked={hideCeiling} onChange={(e) => setHideCeiling(e.target.checked)} data-testid="scan-hide-ceiling" /> {t("scans.hide_ceiling")}</label></div>
          <div>
            <label><input type="checkbox" checked={cut} onChange={(e) => setCut(e.target.checked)} data-testid="scan-cut" /> {t("scans.cut")}</label>
            {cut && (
              <input type="range" min={0.1} max={3} step={0.05} value={cutHeight} aria-label={t("scans.cut")}
                     onChange={(e) => setCutHeight(Number(e.target.value))} style={{ width: "100%" }} />
            )}
            {cut && <span className="muted">{cutHeight.toFixed(2)} m</span>}
          </div>
          <div className="field" style={{ marginTop: 6 }}>
            <label htmlFor="scan-color">{t("scans.color_by")}</label>
            <select id="scan-color" value={colorBy} onChange={(e) => setColorBy(e.target.value as "class" | "pass")}>
              <option value="class">{t("scans.color_class")}</option>
              <option value="pass">{t("scans.color_pass")}</option>
            </select>
          </div>
          {colorBy === "class" ? (
            <ul className="list legend" data-testid="scan-legend">
              {CLASS_COLORS.map((c, i) => (
                <li key={c}><span style={{ display: "inline-block", width: 12, height: 12, background: c, marginRight: 6 }} />
                  {t(`scans.class.${focus?.mesh?.classes[i] ?? ["none", "wall", "floor", "ceiling", "table", "seat", "window", "door"][i]}`)}</li>
              ))}
            </ul>
          ) : (
            <ul className="list legend">
              {shown.map((sha, i) => (
                <li key={sha}><span style={{ display: "inline-block", width: 12, height: 12, background: PASS_COLORS[i % PASS_COLORS.length], marginRight: 6 }} />
                  {tracks.flatMap((tr) => tr.passes).find((p) => p.sha256 === sha)?.name}</li>
              ))}
            </ul>
          )}
          <p className="muted" style={{ margin: 0 }}>
            <span style={{ color: "#0072b2" }}>━</span> {t("scans.legend_kept")} · <span style={{ color: "#d62728" }}>┅</span> {t("scans.legend_discarded")}
          </p>
        </div>

        {focus && (
          <div className="panel" data-testid="scan-summary">
            <h4 style={{ marginTop: 0 }}>{t("scans.summary")}: {String(focus.detail.summary.pass)}</h4>
            <dl className="kv">
              <dt>{t("scans.quality")}</dt><dd>{String(focus.detail.summary.quality)}</dd>
              <dt>{t("scans.frames")}</dt><dd>{String(focus.detail.summary.frames)}</dd>
              <dt>{t("scans.duration")}</dt><dd>{Number(focus.detail.summary.duration_s).toFixed(1)} s</dd>
              <dt>{t("scans.kept")}</dt><dd>{Number(focus.detail.summary.kept_s).toFixed(1)} s</dd>
              <dt>{t("scans.segments")}</dt><dd>{String(focus.detail.summary.segments)}</dd>
              <dt>{t("scans.aligned")}</dt><dd>{focus.detail.summary.aligned ? t("scans.yes") : t("scans.no")}</dd>
              <dt>{t("scans.device")}</dt><dd>{String(focus.detail.summary.device)}</dd>
            </dl>
            {focus.mesh && (
              <p className="muted" style={{ marginBottom: 0 }}>
                {t("scans.faces", { shown: focus.mesh.faces.toLocaleString(), total: focus.mesh.total_faces.toLocaleString() })}
              </p>
            )}
          </div>
        )}
      </aside>
      <section className="main">
        {shown.length === 0 ? (
          <p className="muted" style={{ padding: 24 }}>{selected.length ? t("scans.loading") : t("scans.pick")}</p>
        ) : (
          <Viewport key={shown.join(",")} camera={frame.camera} target={frame.target} testId="scan-viewport">
            {shown.map((sha, i) => {
              const l = loaded[sha];
              if (!l) return null;
              const color = PASS_COLORS[i % PASS_COLORS.length];
              return (
                <group key={sha}>
                  {showMesh && l.mesh && (
                    <ScanMeshView mesh={l.mesh} passColor={colorBy === "pass" ? color : undefined} hideCeiling={hideCeiling}
                                  maxZ={cut ? floorZ + cutHeight : undefined} />
                  )}
                  {showPath && <ScanTrajectory detail={l.detail} color={colorBy === "pass" ? color : "#0072b2"} />}
                </group>
              );
            })}
            <gridHelper args={[20, 20, "#888", "#bbb"]} rotation-x={Math.PI / 2} position={[frame.target[0], frame.target[1], floorZ]} />
          </Viewport>
        )}
      </section>
    </div>
  );
}
