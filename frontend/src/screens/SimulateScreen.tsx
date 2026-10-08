import { Line } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useRef, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { Vector3 } from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { api, type ControllerInfo, type FrameMessage, type SimStart } from "../api/client";
import { interpolate, type TimedPoses } from "../sim/interp";
import { useSimulation } from "../sim/useSimulation";
import { CarModel, type Pose } from "../three/CarModel";
import { TrackModel } from "../three/TrackModel";
import { Viewport } from "../three/Viewport";
import { TeleopPanel } from "../teleop/TeleopPanel";

function Cars({ cars, history }: { cars: NonNullable<ReturnType<typeof useSimulation>["scene"]>["cars"]; history: RefObject<TimedPoses[]> }) {
  const [poses, setPoses] = useState<Record<string, Record<string, Pose>>>({});
  const clock = useRef<{ wall: number; sim: number } | null>(null);
  useFrame(() => {
    const h = history.current;
    const b = h[h.length - 1];
    if (!b) return;
    const a = h[h.length - 2] ?? b;
    const now = performance.now() / 1000;
    if (!clock.current || clock.current.sim !== b.t) clock.current = { wall: now, sim: b.t };
    const t = a.t + Math.min(1, (now - clock.current.wall) * 30) * (b.t - a.t);
    setPoses(interpolate(a, b, t));
  });
  return <>{cars.map((c) => <CarModel key={c.name} car={c} poses={poses[c.name]} />)}</>;
}

function Follow({ frame, enabled, controls }: { frame: FrameMessage | null; enabled: boolean; controls: RefObject<OrbitControlsImpl | null> }) {
  const tmp = useRef(new Vector3());
  useFrame(({ camera }) => {
    const c = frame?.bodies["ego"]?.["chassis"];
    if (!enabled || !c || !controls.current) return;
    const [x, y] = c[0];
    const [w, , , z] = c[1];
    const yaw = 2 * Math.atan2(z, w);
    controls.current.target.set(x, y, 0.05);
    tmp.current.set(x - Math.cos(yaw) * 0.8, y - Math.sin(yaw) * 0.8, 0.9);
    camera.position.lerp(tmp.current, 0.1);
  });
  return null;
}

function Sensors({ frame }: { frame: FrameMessage | null }) {
  if (!frame) return null;
  const { ultrasonic, lidar_points } = frame.ego;
  return (
    <group>
      {Object.entries(ultrasonic).map(([name, u]) => {
        const len = u.value ?? 0.3;
        const end: [number, number, number] = [u.origin[0] + u.direction[0] * len, u.origin[1] + u.direction[1] * len, u.origin[2] + u.direction[2] * len];
        return <Line key={name} points={[u.origin, end]} color={u.value == null ? "#d55e00" : "#0072b2"} lineWidth={2} />;
      })}
      {lidar_points.length > 0 && (
        <points>
          <bufferGeometry>
            <bufferAttribute attach="attributes-position" args={[new Float32Array(lidar_points.flatMap(([x, y]) => [x, y, 0.1])), 3]} />
          </bufferGeometry>
          <pointsMaterial size={0.03} color="#e69f00" />
        </points>
      )}
    </group>
  );
}

/** Quick-start car (spec 0002 defaults): 30° steering lock; teleop speed limit range in the sim. */
const MAX_STEER_RAD = (30 * Math.PI) / 180;
const SIM_MAX_SPEED = 1.5;

export function SimulateScreen() {
  const { t } = useTranslation();
  const sim = useSimulation();
  const [controllers, setControllers] = useState<ControllerInfo[]>([]);
  const [form, setForm] = useState({ controller: "", seed: 0, loop: true, length: 40, laps: 1, opponents: 0, record: false });
  const [speed, setSpeed] = useState(1);
  const [follow, setFollow] = useState(true);
  const [showSensors, setShowSensors] = useState(true);
  const [cut, setCut] = useState(true);
  const controls = useRef<OrbitControlsImpl | null>(null);

  useEffect(() => {
    void api.controllers().then((cs) => {
      setControllers(cs);
      setForm((f) => ({ ...f, controller: f.controller || cs.find((c) => c.name === "centering")?.path || cs[0]?.path || "" }));
    });
  }, []);

  const running = sim.status === "running" || sim.status === "paused" || sim.status === "connecting";
  const startRun = () => {
    const req: SimStart = {
      type: "start",
      controller: form.controller,
      corridor: { seed: form.seed, loop: form.loop, length_m: form.length } as SimStart["corridor"],
      laps: form.laps,
      opponents: form.opponents,
      seed: form.seed,
      speed,
      record_path: form.record ? `runs/run-${Date.now()}.mcap` : null,
    };
    sim.start(req);
  };
  const f = sim.frame;

  return (
    <div className="screen">
      <aside className="side">
        <div className="field">
          <label htmlFor="ctrl">{t("simulate.controller")}</label>
          <select id="ctrl" data-testid="sim-controller" value={form.controller} onChange={(e) => setForm({ ...form, controller: e.target.value })}>
            {controllers.map((c) => <option key={c.path} value={c.path}>{c.name}{c.template ? "" : " (file)"}</option>)}
            <option value="none">{t("simulate.no_controller")}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="seed">{t("simulate.corridor_seed")}</label>
          <input id="seed" type="number" value={form.seed} onChange={(e) => setForm({ ...form, seed: Number(e.target.value) })} />
        </div>
        <div className="field"><label><input type="checkbox" checked={form.loop} onChange={(e) => setForm({ ...form, loop: e.target.checked })} /> {t("simulate.loop")}</label></div>
        <div className="field">
          <label htmlFor="len">{t("simulate.length")}</label>
          <input id="len" type="number" min={20} max={120} value={form.length} onChange={(e) => setForm({ ...form, length: Number(e.target.value) })} />
        </div>
        <div className="field">
          <label htmlFor="laps">{t("simulate.laps")}</label>
          <input id="laps" data-testid="sim-laps" type="number" min={1} max={20} value={form.laps} onChange={(e) => setForm({ ...form, laps: Number(e.target.value) })} />
        </div>
        <div className="field">
          <label htmlFor="opp">{t("simulate.opponents")}</label>
          <input id="opp" type="number" min={0} max={5} value={form.opponents} onChange={(e) => setForm({ ...form, opponents: Number(e.target.value) })} />
        </div>
        <div className="field"><label><input type="checkbox" data-testid="sim-record" checked={form.record} onChange={(e) => setForm({ ...form, record: e.target.checked })} /> {t("simulate.record")}</label></div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
          {!running && <button className="primary" data-testid="sim-start" onClick={startRun} disabled={!form.controller}>{t("simulate.start")}</button>}
          {sim.status === "running" && <button onClick={sim.pause}>{t("simulate.pause")}</button>}
          {sim.status === "paused" && <button onClick={sim.resume}>{t("simulate.resume")}</button>}
          {running && <button onClick={sim.stop}>{t("simulate.stop")}</button>}
        </div>
        <div className="field">
          <label htmlFor="speed">{t("simulate.speed")}</label>
          <select id="speed" data-testid="sim-speed" value={speed} onChange={(e) => { const s = Number(e.target.value); setSpeed(s); sim.setSpeed(s); }}>
            {[1, 2, 5, 1000].map((s) => <option key={s} value={s}>{s === 1000 ? "max" : `${s}×`}</option>)}
          </select>
        </div>
        <div className="field"><label><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> {t("simulate.follow")}</label></div>
        <div className="field"><label><input type="checkbox" checked={cut} onChange={(e) => setCut(e.target.checked)} /> {t("simulate.cutaway")}</label></div>
        <div className="field"><label><input type="checkbox" checked={showSensors} onChange={(e) => setShowSensors(e.target.checked)} /> {t("simulate.sensors")}</label></div>
        {running && (
          <TeleopPanel
            send={(msg) => sim.send(msg)}
            onStop={() => sim.send({ type: "stop_car" })}
            maxSteer={MAX_STEER_RAD}
            maxSpeed={SIM_MAX_SPEED}
            disabled={sim.status !== "running"}
            state={f?.ego.state}
          />
        )}
        {sim.error && <p className="error">{sim.error}</p>}
        {sim.result && (
          <div className="panel" data-testid="sim-result">
            <strong>{t("simulate.result")}: </strong>
            <span className={`badge ${sim.result.finished ? "ok" : "bad"}`}>{sim.result.finished ? t("simulate.finished") : t("simulate.not_finished")}</span>
            <div>{t("simulate.laps_done")}: <span data-testid="lap-times">{sim.result.lap_times_s.map((x) => `${x.toFixed(1)} s`).join(", ") || "–"}</span></div>
            <div>{t("simulate.wall_contacts")}: {sim.result.wall_contacts}</div>
            {sim.result.record_path && <div className="muted" data-testid="record-path">{sim.result.record_path}</div>}
          </div>
        )}
        <h4>{t("simulate.events")}</h4>
        <ul className="events list">
          {sim.events.map((e, i) => <li key={i}>{e.t.toFixed(1)} s · {e.car} · {e.kind} {e.other}</li>)}
        </ul>
      </aside>
      <section className="main">
        {sim.scene ? (
          <Viewport camera={[0, -2, 4]} target={[0, 0, 0]} controlsRef={controls} testId="sim-viewport">
            <TrackModel primitives={sim.scene.primitives} cut={cut} />
            <Cars cars={sim.scene.cars} history={sim.history} />
            {sim.trail.length > 1 && <Line points={sim.trail.map(([x, y]) => [x, y, 0.005] as [number, number, number])} color="#cc79a7" lineWidth={2} />}
            {showSensors && <Sensors frame={f} />}
            <Follow frame={f} enabled={follow} controls={controls} />
          </Viewport>
        ) : <div className="muted" style={{ padding: 24 }}>{sim.status === "connecting" ? t("common.loading") : ""}</div>}
        <div className="statusbar" data-testid="sim-status">
          <span>{t("simulate.time")}: {f ? `${f.t.toFixed(1)} s` : "–"}</span>
          <span>{t("simulate.state")}: <strong data-testid="sim-state">{f?.ego.state ?? "–"}</strong></span>
          <span>{t("simulate.distance")}: {f ? `${f.ego.distance_m.toFixed(1)} m` : "–"}</span>
          <span>{t("simulate.laps_done")}: {f ? `${f.ego.laps}/${form.laps}` : "–"}</span>
          {f && Object.entries(f.ego.channels).slice(0, 6).map(([k, v]) => <span key={k} className="muted">{k}: {typeof v === "number" ? v.toFixed(3) : String(v)}</span>)}
        </div>
      </section>
    </div>
  );
}
