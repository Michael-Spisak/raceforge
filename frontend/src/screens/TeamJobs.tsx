import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, type Schemas, workspace } from "../api/client";

type JobInfo = Schemas["JobInfo"];
type WorkerInfo = Schemas["WorkerInfo"];

const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));
const fmt = (v: unknown) => (typeof v === "number" ? v.toFixed(1) : "–");

/** Team workers and the jobs queued for them (spec 0020). ``refresh`` changes when a job was queued. */
export function TeamJobs({ refresh, controllerPath }: { refresh: number; controllerPath: string }) {
  const { t } = useTranslation();
  const [workers, setWorkers] = useState<WorkerInfo[]>([]);
  const [jobs, setJobs] = useState<JobInfo[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    void workspace.teamWorkers().then(setWorkers).catch(() => undefined);
    void workspace.teamJobs().then(setJobs).catch((e: unknown) => setError(message(e)));
  }, []);
  useEffect(() => load(), [load, refresh]);
  const active = jobs.some((j) => j.status === "queued" || j.status === "running");
  useEffect(() => {
    const id = window.setInterval(load, active ? 3000 : 15000);
    return () => window.clearInterval(id);
  }, [active, load]);

  const saveParams = (job: JobInfo) => {
    const base = controllerPath.replace(/\.py$/, "") || job.controller_name.replace(/\.py$/, "");
    const path = `${base}.tuned.yaml`;
    workspace.saveJobParams(job.id, path).then((r) => setNote(t("train.params_saved", { path: r.path }))).catch((e: unknown) => setError(message(e)));
  };

  return (
    <div className="panel" data-testid="team-jobs">
      <h4 style={{ marginTop: 0 }}>{t("train.team_workers")}</h4>
      {workers.length === 0 ? <p className="muted">{t("train.no_workers")}</p> : (
        <ul className="list">
          {workers.map((w) => (
            <li key={w.id}>
              {w.online ? "🟢" : "⚪"} {w.name} <span className="muted">· {w.online ? (w.busy ? t("train.worker_busy") : t("train.worker_idle")) : t("train.worker_offline")} · {w.created_by}</span>
            </li>
          ))}
        </ul>
      )}
      <h4>{t("train.team_jobs")}</h4>
      {jobs.length === 0 && <p className="muted">{t("train.none")}</p>}
      <table className="table">
        <tbody>
          {jobs.map((j) => {
            const total = Number(j.progress.total ?? 0);
            const done = Number(j.progress.done ?? 0);
            const score = j.result?.score;
            return (
              <tr key={j.id}>
                <td>{t(`train.mode_${j.kind}`)} · {j.controller_name}<div className="muted">{j.created_by}{j.worker_name ? ` → ${j.worker_name}` : ""}</div></td>
                <td>
                  {t(`train.team_state_${j.status}`)}
                  {j.status === "running" && total > 0 && ` ${done}/${total}`}
                  {j.status === "error" && <div className="error">{j.error}</div>}
                </td>
                <td>{score != null ? t("train.score", { score: fmt(score) }) : ""}</td>
                <td>
                  {(j.status === "queued" || j.status === "running") && !j.cancel_requested && (
                    <button type="button" onClick={() => void workspace.cancelTeamJob(j.id).then(load)}>{t("train.cancel")}</button>
                  )}
                  {j.status === "done" && j.kind === "tune" && <button type="button" onClick={() => saveParams(j)}>{t("train.save_params")}</button>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="muted">{t("train.worker_help")}</p>
      {note && <p>{note}</p>}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
