import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, raceControl } from "../api/client";
import { type Race, fmtMs, incident, lap, newRace, setDnf, standings, undoLap } from "../race/race";
import { useWorkspace } from "../store/workspace";

const LIGHTS = 5;
const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

function tone(freq: number, ms: number): void {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    osc.frequency.value = freq;
    osc.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + ms / 1000);
    osc.onended = () => void ctx.close();
  } catch {
    /* no audio */
  }
}

/** Race Control for our team (spec 0031): start sequence, race clock, laps, incidents, results. */
export function RaceControlScreen() {
  const { t } = useTranslation();
  const loggedIn = !!useWorkspace((st) => st.status?.logged_in && st.status.workspace);
  const [names, setNames] = useState("car-a\ncar-b");
  const [laps, setLaps] = useState(3);
  const [stepS, setStepS] = useState(1);
  const [sound, setSound] = useState(true);
  const [race, setRace] = useState<Race | null>(null);
  const [lit, setLit] = useState(-1); // -1: no sequence, 0..LIGHTS: red lights on, LIGHTS+1: green
  const [now, setNow] = useState(Date.now());
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const timers = useRef<number[]>([]);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 50);
    return () => window.clearInterval(id);
  }, []);
  useEffect(() => () => timers.current.forEach((x) => window.clearTimeout(x)), []);

  const start = () => {
    const cars = names.split(/\n|,/).map((s) => s.trim()).filter(Boolean);
    if (!cars.length) return;
    setRace(newRace(cars, laps));
    setNote("");
    setError("");
    setLit(0);
    const step = stepS * 1000;
    for (let k = 1; k <= LIGHTS; k++) {
      timers.current.push(window.setTimeout(() => { setLit(k); if (sound) tone(440, 150); }, k * step));
    }
    // lights out after a short random hold (as in motorsport), then GO
    const go = (LIGHTS + 1) * step + Math.random() * step;
    timers.current.push(window.setTimeout(() => {
      setLit(LIGHTS + 1);
      if (sound) tone(880, 600);
      setRace((r) => (r ? { ...r, startedAt: Date.now() } : r));
      timers.current.push(window.setTimeout(() => setLit(-1), 2500));
    }, go));
  };
  const abort = () => {
    timers.current.forEach((x) => window.clearTimeout(x));
    timers.current = [];
    setLit(-1);
    setRace(null);
  };
  const save = () => {
    if (!race || race.startedAt == null) return;
    setError("");
    raceControl.save({ laps: race.laps, started_at_ms: race.startedAt, cars: race.cars, standings: standings(race) })
      .then((r) => setNote(t("rc.saved", { run: r.run })))
      .catch((e: unknown) => setError(message(e)));
  };

  const elapsed = race?.startedAt != null ? now - race.startedAt : null;
  const table = race ? standings(race) : [];

  return (
    <div className="screen">
      {lit >= 0 && (
        <div data-testid="rc-lights" onClick={lit > LIGHTS ? () => setLit(-1) : undefined}
             style={{ position: "fixed", inset: 0, background: "#000", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", gap: "3vw" }}>
          {Array.from({ length: LIGHTS }, (_, k) => (
            <div key={k} style={{
              width: "14vw", height: "14vw", borderRadius: "50%",
              background: lit > LIGHTS ? "#1db954" : k < lit ? "#e10600" : "#222",
              boxShadow: lit > LIGHTS || k < lit ? "0 0 4vw currentColor" : "none",
            }} />
          ))}
          {lit > LIGHTS && <div style={{ position: "absolute", bottom: "10vh", color: "#fff", fontSize: "8vw", fontWeight: 700 }}>GO</div>}
        </div>
      )}
      <aside className="side">
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>{t("rc.title")}</h3>
          <div className="field">
            <label htmlFor="rc-cars">{t("rc.cars")}</label>
            <textarea id="rc-cars" rows={4} value={names} onChange={(e) => setNames(e.target.value)} disabled={!!race} />
          </div>
          <div className="field">
            <label htmlFor="rc-laps">{t("rc.laps")}</label>
            <input id="rc-laps" type="number" min={1} max={50} value={laps} onChange={(e) => setLaps(Number(e.target.value))} disabled={!!race} />
          </div>
          <div className="field">
            <label htmlFor="rc-step">{t("rc.step")}</label>
            <input id="rc-step" type="number" min={0.5} max={3} step={0.5} value={stepS} onChange={(e) => setStepS(Number(e.target.value))} disabled={!!race} />
          </div>
          <label><input type="checkbox" checked={sound} onChange={(e) => setSound(e.target.checked)} /> {t("rc.sound")}</label>
          <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
            {!race && <button type="button" className="primary" onClick={start} data-testid="rc-start">{t("rc.start")}</button>}
            {race && <button type="button" onClick={abort}>{t("rc.new_race")}</button>}
            {race?.startedAt != null && loggedIn && <button type="button" onClick={save}>{t("rc.save")}</button>}
          </div>
          {note && <p>{note}</p>}
          {error && <p className="error">{error}</p>}
          <p className="muted">{t("rc.hint")}</p>
        </div>
      </aside>
      <section className="main" style={{ display: "block", overflow: "auto", padding: 12 }}>
        <div className="panel" style={{ fontSize: 48, fontVariantNumeric: "tabular-nums" }} data-testid="rc-clock">
          {elapsed == null ? (race ? t("rc.waiting") : "–") : fmtMs(elapsed)}
        </div>
        {race && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
            {race.cars.map((c, i) => {
              const done = c.laps.length >= race.laps;
              return (
                <div key={c.name} className="panel" style={{ margin: 0 }}>
                  <strong>{c.name}</strong>{" "}
                  <span className="muted">{t("rc.lap_of", { n: c.laps.length, total: race.laps })}{c.dnf ? ` · ${t("rc.dnf")}` : done ? ` · ${t("rc.finished")}` : ""}</span>
                  <button type="button" className="primary" style={{ display: "block", width: "100%", fontSize: 28, padding: "18px 0", margin: "8px 0" }}
                          disabled={race.startedAt == null || done || c.dnf} onClick={() => setRace((r) => (r ? lap(r, i, Date.now()) : r))}
                          data-testid={`rc-lap-${i}`}>
                    {t("rc.lap")}
                  </button>
                  <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                    <button type="button" onClick={() => setRace((r) => (r ? undoLap(r, i) : r))} disabled={!c.laps.length}>{t("rc.undo")}</button>
                    <button type="button" onClick={() => {
                      const text = window.prompt(t("rc.incident_prompt", { car: c.name }));
                      if (text) setRace((r) => (r ? incident(r, i, Date.now(), text) : r));
                    }}>{t("rc.incident")}</button>
                    <button type="button" onClick={() => setRace((r) => (r ? setDnf(r, i, !c.dnf) : r))}>{c.dnf ? t("rc.undnf") : t("rc.dnf")}</button>
                  </div>
                  <ol className="muted" style={{ fontSize: 12 }}>
                    {c.laps.map((x, k) => <li key={k}>{fmtMs(x - (k ? (c.laps[k - 1] ?? 0) : 0))}</li>)}
                  </ol>
                  {c.incidents.map((x, k) => <p key={k} className="warning" style={{ fontSize: 12 }}>{fmtMs(x.at)} {x.text}</p>)}
                </div>
              );
            })}
          </div>
        )}
        {table.length > 0 && (
          <div className="panel">
            <h4 style={{ marginTop: 0 }}>{t("rc.standings")}</h4>
            <table className="table" data-testid="rc-standings">
              <thead><tr><th>#</th><th>{t("rc.car")}</th><th>{t("rc.laps")}</th><th>{t("rc.time")}</th><th>{t("rc.best_lap")}</th></tr></thead>
              <tbody>
                {table.map((s, k) => (
                  <tr key={s.name}>
                    <td>{s.status === "dnf" ? "–" : k + 1}</td><td>{s.name}</td><td>{s.laps}</td>
                    <td>{s.status === "dnf" ? t("rc.dnf") : fmtMs(s.time_ms)}</td><td>{fmtMs(s.best_lap_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
