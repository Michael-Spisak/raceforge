import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, workspace, type ControllerInfo, type QuickTrackInfo, type TrainJob, type TrainRace } from "../api/client";
import { useWorkspace } from "../store/workspace";
import { TeamJobs } from "./TeamJobs";

const fmt = (v: number | null | undefined, digits = 1) => (v == null ? "–" : v.toFixed(digits));
const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

/** Benchmark and tune controllers in the sim (spec 0013). One job at a time; progress is polled. */
export function TrainScreen() {
  const { t } = useTranslation();
  const [controllers, setControllers] = useState<ControllerInfo[]>([]);
  const [controller, setController] = useState("");
  const [mode, setMode] = useState<"benchmark" | "tune">("benchmark");
  const [params, setParams] = useState("");
  const [trials, setTrials] = useState(30);
  const [trainTracks, setTrainTracks] = useState(3);
  const [race, setRace] = useState<TrainRace>({ tracks: 5, length_m: 25, laps: 1, opponents: 0, max_time_s: 240, quick_track: null });
  const [quickTracks, setQuickTracks] = useState<QuickTrackInfo[]>([]);
  const [jobs, setJobs] = useState<TrainJob[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const loggedIn = !!useWorkspace((st) => st.status?.logged_in && st.status.workspace);
  const [runOn, setRunOn] = useState<"local" | "team">("local");
  const [teamRefresh, setTeamRefresh] = useState(0);

  const load = useCallback(() => api.trainJobs().then(setJobs).catch(() => undefined), []);
  useEffect(() => {
    void api.controllers().then((cs) => {
      setControllers(cs);
      setController((c) => c || cs.find((x) => x.name === "centering")?.path || cs[0]?.path || "");
    }).catch(() => undefined);
    void api.quickTracks().then((qs) => setQuickTracks(qs.filter((q) => !q.error))).catch(() => undefined);
    void load();
  }, [load]);
  const running = jobs.some((j) => j.state === "running");
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => void load(), 1000);
    return () => window.clearInterval(id);
  }, [running, load]);

  const start = (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (runOn === "team") {
      const job = mode === "benchmark"
        ? { bench: { controller, params: params || null, race } }
        : { tune: { controller, trials, train_tracks: trainTracks, timeout_s: null, out: null, race } };
      workspace.submitTeamJob(job).then(() => setTeamRefresh((n) => n + 1)).catch((err: unknown) => setError(message(err)));
      return;
    }
    const req = mode === "benchmark"
      ? api.trainBenchmark({ controller, params: params || null, race })
      : api.trainTune({ controller, trials, train_tracks: trainTracks, timeout_s: null, out: null, race });
    req.then((job) => { setSelected(job.id); return load(); }).catch((err: unknown) => setError(message(err)));
  };

  const num = (key: Exclude<keyof TrainRace, "quick_track">, label: string, min: number, max: number, step = 1) => (
    <div className="field">
      <label htmlFor={`train-${key}`}>{label}</label>
      <input id={`train-${key}`} type="number" min={min} max={max} step={step} value={race[key] ?? ""}
             onChange={(e) => setRace({ ...race, [key]: Number(e.target.value) })} />
    </div>
  );

  const job = jobs.find((j) => j.id === selected) ?? jobs[0];

  return (
    <div className="screen">
      <aside className="side">
        <form className="panel" onSubmit={start}>
          <h3 style={{ marginTop: 0 }}>{t("train.setup")}</h3>
          <div className="field">
            <label htmlFor="train-ctrl">{t("train.controller")}</label>
            <select id="train-ctrl" data-testid="train-controller" value={controller} onChange={(e) => setController(e.target.value)}>
              {controllers.map((c) => <option key={c.path} value={c.path}>{c.name}</option>)}
            </select>
          </div>
          <div className="field">
            <label>{t("train.mode")}</label>
            <div style={{ display: "flex", gap: 12 }}>
              <label><input type="radio" checked={mode === "benchmark"} onChange={() => setMode("benchmark")} /> {t("train.mode_benchmark")}</label>
              <label><input type="radio" checked={mode === "tune"} onChange={() => setMode("tune")} data-testid="train-mode-tune" /> {t("train.mode_tune")}</label>
            </div>
          </div>
          {mode === "benchmark" ? (
            <div className="field">
              <label htmlFor="train-params">{t("train.params")}</label>
              <input id="train-params" value={params} onChange={(e) => setParams(e.target.value)} />
            </div>
          ) : (
            <>
              <div className="field">
                <label htmlFor="train-trials">{t("train.trials")}</label>
                <input id="train-trials" type="number" min={1} max={1000} value={trials} onChange={(e) => setTrials(Number(e.target.value))} />
              </div>
              <div className="field">
                <label htmlFor="train-train-tracks">{t("train.train_tracks")}</label>
                <input id="train-train-tracks" type="number" min={1} max={50} value={trainTracks} onChange={(e) => setTrainTracks(Number(e.target.value))} />
              </div>
            </>
          )}
          <div className="field">
            <label htmlFor="train-track">{t("simulate.track")}</label>
            <select id="train-track" data-testid="train-track" value={race.quick_track ?? ""}
                    onChange={(e) => setRace({ ...race, quick_track: e.target.value || null })}>
              <option value="">{t("train.procedural")}</option>
              {quickTracks.map((q) => <option key={q.name} value={q.name}>{q.name}</option>)}
            </select>
          </div>
          {num("tracks", race.quick_track ? t("train.runs") : t("train.tracks"), 1, 100)}
          {!race.quick_track && num("length_m", t("train.length"), 20, 120, 5)}
          {num("laps", t("train.laps"), 1, 10)}
          {num("opponents", t("train.opponents"), 0, 5)}
          {num("max_time_s", t("train.max_time"), 10, 3600, 10)}
          {loggedIn && (
            <div className="field">
              <label htmlFor="train-run-on">{t("train.run_on")}</label>
              <select id="train-run-on" data-testid="train-run-on" value={runOn} onChange={(e) => setRunOn(e.target.value as "local" | "team")}>
                <option value="local">{t("train.run_local")}</option>
                <option value="team">{t("train.run_team")}</option>
              </select>
            </div>
          )}
          <button type="submit" className="primary" disabled={(runOn === "local" && running) || !controller} data-testid="train-start">{t("train.start")}</button>
          {error && <p className="error" role="alert">{error}</p>}
          <p className="muted">{t("train.help")}</p>
        </form>
        <div className="panel">
          <h4 style={{ marginTop: 0 }}>{t("train.jobs")}</h4>
          {jobs.length === 0 && <p className="muted">{t("train.none")}</p>}
          <ul className="list" data-testid="train-jobs">
            {jobs.map((j) => (
              <li key={j.id}>
                <button type="button" className={j.id === job?.id ? "primary" : ""} onClick={() => setSelected(j.id)}>
                  {t(`train.mode_${j.kind}`)} · {j.controller.split(/[\\/]/).pop()} · {t(`train.state_${j.state}`)}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </aside>
      <section className="main" style={{ display: "block", overflow: "auto", padding: 12 }}>
        {loggedIn && runOn === "team" && <TeamJobs refresh={teamRefresh} controllerPath={controller} />}
        {job && <JobView job={job} />}
      </section>
    </div>
  );
}

function JobView({ job }: { job: TrainJob }) {
  const { t } = useTranslation();
  const done = job.kind === "benchmark" ? job.runs.length : Math.max(0, job.trials.length - 1);
  const overfit = job.state === "done" && job.kind === "tune" && job.score != null && job.default_score != null && job.score > job.default_score;
  return (
    <div className="panel" data-testid="train-job">
      <h3 style={{ marginTop: 0 }}>
        {t(`train.mode_${job.kind}`)} · {job.controller.split(/[\\/]/).pop()} · <span data-testid="train-job-state">{t(`train.state_${job.state}`)}</span>
      </h3>
      <p>
        {t("train.progress", { done, total: job.total })}
        {job.state === "running" && (
          <button type="button" style={{ marginLeft: 8 }} onClick={() => void api.trainCancel(job.id)}>{t("train.cancel")}</button>
        )}
      </p>
      {job.error && <p className="error">{job.error}</p>}
      {job.score != null && (
        <p data-testid="train-score">
          <strong>{t("train.score", { score: fmt(job.score) })}</strong>
          {job.finished_rate != null && ` · ${t("train.finished_rate", { rate: Math.round(job.finished_rate * 100) })}`}
          {job.kind === "tune" && ` · ${t("train.vs_default", { score: fmt(job.score), def: fmt(job.default_score) })}`}
        </p>
      )}
      {overfit && <p className="warning">{t("train.overfit")}</p>}
      {job.out && <p data-testid="train-out">{t("train.written", { path: job.out })}</p>}
      {job.kind === "tune" && job.trials.length > 0 && (
        <table className="table">
          <thead><tr><th>{t("train.trial")}</th><th>Score</th><th>{t("train.best")}</th></tr></thead>
          <tbody>
            {[...job.trials].reverse().slice(0, 50).map((tr) => (
              <tr key={tr.number}>
                <td>{tr.number < 0 ? t("train.default") : tr.number + 1}</td>
                <td>{fmt(tr.score)}</td>
                <td>{fmt(tr.best)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {job.runs.length > 0 && (
        <table className="table" data-testid="train-runs">
          <thead><tr><th>{t("train.corridor")}</th><th>{t("train.result")}</th><th>{t("train.walls")}</th></tr></thead>
          <tbody>
            {job.runs.map((r) => (
              <tr key={r.seed}>
                <td>{r.seed}</td>
                <td>{r.finished ? `${fmt(r.time_s)} s` : t("train.dnf", { pct: Math.round(r.fraction * 100) })}{r.error ? ` · ${r.error}` : ""}</td>
                <td>{r.wall_contacts}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
