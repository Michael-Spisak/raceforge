import { Fragment, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, fleet, type Schemas } from "../api/client";
import type { CarFrame } from "./useCarLink";

type Sample = Schemas["CalibrationSample"];
type Result = Schemas["CalibrationResult"];
type Recording = "straight" | "left" | "right" | null;

const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

/** Calibration wizards (spec 0033): drive straight, full-lock circles; results go into car.yaml. */
export function CalibrationPanel({ frame, lastFrameAt, connected }: { frame: CarFrame | null; lastFrameAt: number; connected: boolean }) {
  const { t } = useTranslation();
  const [cars, setCars] = useState<Schemas["FleetCar"][]>([]);
  const [car, setCar] = useState(() => localStorage.getItem("rf.calib.car") ?? "");
  const [newName, setNewName] = useState("");
  const [recording, setRecording] = useState<Recording>(null);
  const samples = useRef<Record<Exclude<Recording, null>, Sample[]>>({ straight: [], left: [], right: [] });
  const [counts, setCounts] = useState({ straight: 0, left: 0, right: 0 });
  const [distance, setDistance] = useState(2.0);
  const [result, setResult] = useState<Result | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  const load = () => void fleet.list().then((cs) => { setCars(cs); setCar((c) => c || cs[0]?.name || ""); }).catch(() => undefined);
  useEffect(load, []);
  useEffect(() => {
    if (!recording || !frame) return;
    samples.current[recording].push({ t_ms: lastFrameAt, frame: frame as Record<string, unknown> });
    setCounts((c) => ({ ...c, [recording]: samples.current[recording].length }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastFrameAt]);

  const record = (what: Exclude<Recording, null>) => {
    if (recording === what) return setRecording(null);
    samples.current[what] = [];
    setCounts((c) => ({ ...c, [what]: 0 }));
    setResult(null);
    setRecording(what);
  };
  const run = (p: Promise<Result>) => {
    setError("");
    setNote("");
    p.then(setResult).catch((e: unknown) => setError(message(e)));
  };
  const apply = () => {
    if (!result) return;
    fleet.apply(car, result.changes).then(() => { setNote(t("calib.applied", { car })); setResult(null); }).catch((e: unknown) => setError(message(e)));
  };
  const create = () => {
    fleet.create(newName.trim()).then((c) => { setNewName(""); setCar(c.name); localStorage.setItem("rf.calib.car", c.name); load(); }).catch((e: unknown) => setError(message(e)));
  };

  const btn = (what: Exclude<Recording, null>, label: string) => (
    <button type="button" onClick={() => record(what)} disabled={!connected || !car || (recording !== null && recording !== what)}>
      {recording === what ? t("calib.stop", { n: counts[what] }) : label}{recording !== what && counts[what] ? ` (${counts[what]})` : ""}
    </button>
  );

  return (
    <div className="panel" data-testid="calibration">
      <h4 style={{ marginTop: 0 }}>{t("calib.title")}</h4>
      <div className="field">
        <label htmlFor="calib-car">{t("calib.car")}</label>
        <select id="calib-car" value={car} onChange={(e) => { setCar(e.target.value); localStorage.setItem("rf.calib.car", e.target.value); }}>
          <option value="" disabled>–</option>
          {cars.map((c) => <option key={c.name} value={c.name}>{c.name}{c.error ? " ⚠" : ""}</option>)}
        </select>
        <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
          <input placeholder={t("calib.new_car")} value={newName} onChange={(e) => setNewName(e.target.value)} />
          <button type="button" onClick={create} disabled={!newName.trim()}>{t("calib.add")}</button>
        </div>
      </div>
      <p className="muted">{t("calib.straight_help")}</p>
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
        {btn("straight", t("calib.record_straight"))}
        <label>{t("calib.distance")} <input type="number" min={0.5} max={20} step={0.01} style={{ width: 70 }} value={distance} onChange={(e) => setDistance(Number(e.target.value))} /> m</label>
        <button type="button" disabled={!counts.straight || recording !== null}
                onClick={() => run(fleet.calibrateStraight({ car, samples: samples.current.straight, true_distance_m: distance }))}>{t("calib.evaluate")}</button>
      </div>
      <p className="muted">{t("calib.circle_help")}</p>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {btn("left", t("calib.record_left"))}
        {btn("right", t("calib.record_right"))}
        <button type="button" disabled={!counts.left || !counts.right || recording !== null}
                onClick={() => run(fleet.calibrateCircle({ car, left: samples.current.left, right: samples.current.right }))}>{t("calib.evaluate")}</button>
      </div>
      {result && (
        <div data-testid="calib-result" style={{ marginTop: 8 }}>
          <dl className="kv">
            {Object.entries(result.details).map(([k, v]) => (
              <Fragment key={k}><dt>{t(`calib.d_${k}`)}</dt><dd>{String(v)}</dd></Fragment>
            ))}
          </dl>
          <p>{t("calib.suggested")} {Object.entries(result.changes).map(([k, v]) => `${k} = ${v}`).join(", ")}</p>
          <button type="button" className="primary" onClick={apply}>{t("calib.apply", { car })}</button>
        </div>
      )}
      {note && <p>{note}</p>}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
