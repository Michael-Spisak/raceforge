import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, type Schemas, workspace } from "../api/client";

type Status = Schemas["LocalWorkerStatus"];
type Policy = Schemas["WorkerPolicy"];
type Day = Schemas["ScheduleWindow"]["days"][number];

const DAYS: Day[] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

/** "Use this computer as a team worker" (spec 0020 part C): switch, mode, idle time, one schedule window. */
export function LocalWorkerPanel() {
  const { t } = useTranslation();
  const [st, setSt] = useState<Status | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => void workspace.localWorker().then(setSt).catch((e: unknown) => setError(message(e))), []);
  useEffect(() => {
    load();
    const id = window.setInterval(load, 5000);
    return () => window.clearInterval(id);
  }, [load]);
  if (!st) return null;

  const save = (enabled: boolean, policy: Policy) => {
    setError("");
    workspace.setLocalWorker({ enabled, policy }).then(setSt).catch((e: unknown) => setError(message(e)));
  };
  const p = st.policy;
  const win = p.schedule[0] ?? { days: ["mon", "tue", "wed", "thu", "fri"] as Day[], start: "18:00", end: "07:00" };
  const setWin = (w: Partial<typeof win>) => save(st.enabled, { ...p, schedule: [{ ...win, ...w }] });

  return (
    <div className="panel" data-testid="local-worker">
      <h3>{t("worker.title")}</h3>
      <label>
        <input type="checkbox" checked={st.enabled} data-testid="local-worker-enabled" onChange={(e) => save(e.target.checked, p)} />{" "}
        {t("worker.enable")}
      </label>
      <div className="field">
        <label htmlFor="worker-mode">{t("worker.mode")}</label>
        <select id="worker-mode" value={p.mode} onChange={(e) => save(st.enabled, { ...p, mode: e.target.value as Policy["mode"] })}>
          {(["idle", "always", "schedule", "paused"] as const).map((m) => <option key={m} value={m}>{t(`worker.mode_${m}`)}</option>)}
        </select>
      </div>
      {p.mode === "idle" && (
        <div className="field">
          <label htmlFor="worker-idle">{t("worker.idle_minutes")}</label>
          <input id="worker-idle" type="number" min={1} max={240} value={p.idle_minutes}
                 onChange={(e) => save(st.enabled, { ...p, idle_minutes: Number(e.target.value) })} />
        </div>
      )}
      {p.mode === "schedule" && (
        <div className="field">
          <span>{t("worker.schedule")}</span>
          <div>
            {DAYS.map((d) => (
              <label key={d} style={{ marginRight: 6 }}>
                <input type="checkbox" checked={win.days.includes(d)}
                       onChange={(e) => setWin({ days: e.target.checked ? [...win.days, d] : win.days.filter((x) => x !== d) })} />
                {t(`worker.day_${d}`)}
              </label>
            ))}
          </div>
          <input type="time" aria-label={t("worker.from")} value={win.start} onChange={(e) => setWin({ start: e.target.value })} />
          {" – "}
          <input type="time" aria-label={t("worker.to")} value={win.end} onChange={(e) => setWin({ end: e.target.value })} />
        </div>
      )}
      <p className="muted">
        {st.available ? t("worker.ready") : t(`worker.reason_${st.reason || "paused"}`)}
        {st.name && ` · ${st.name}`}
      </p>
      {st.enabled && st.log.length > 0 && <pre className="muted" style={{ maxHeight: 120, overflow: "auto" }}>{st.log.slice(-8).join("\n")}</pre>}
      <p className="muted">{t("worker.help")}</p>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
