import { type PointerEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TeleopMessage } from "./input";
import { useTeleop } from "./useTeleop";

const SIZE = 150;

/** On-screen joystick for tablets/phones: drag to drive, lift the finger to release (dead-man). */
function TouchJoystick({ onChange }: { onChange: (x: number, y: number, active: boolean) => void }) {
  const [knob, setKnob] = useState<{ x: number; y: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const move = (e: PointerEvent<HTMLDivElement>) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const half = r.width / 2;
    let x = (e.clientX - r.left - half) / half;
    let y = -(e.clientY - r.top - half) / half;
    const len = Math.hypot(x, y);
    if (len > 1) {
      x /= len;
      y /= len;
    }
    setKnob({ x, y });
    onChange(x, y, true);
  };
  const end = () => {
    setKnob(null);
    onChange(0, 0, false);
  };
  return (
    <div
      ref={ref}
      data-testid="teleop-joystick"
      role="application"
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        move(e);
      }}
      onPointerMove={(e) => knob && move(e)}
      onPointerUp={end}
      onPointerCancel={end}
      style={{
        width: SIZE, height: SIZE, borderRadius: "50%", border: "2px solid var(--border)", position: "relative",
        touchAction: "none", background: "var(--panel)", margin: "8px auto",
      }}
    >
      <div
        style={{
          position: "absolute", width: 44, height: 44, borderRadius: "50%", background: "var(--c1, #0072b2)",
          left: SIZE / 2 - 22 + (knob?.x ?? 0) * (SIZE / 2 - 22), top: SIZE / 2 - 22 - (knob?.y ?? 0) * (SIZE / 2 - 22),
          opacity: knob ? 1 : 0.5,
        }}
      />
    </div>
  );
}

/**
 * Teleop controls shared by the Simulate and Live tabs (spec 0010): arm, speed limit, STOP, touch joystick,
 * live readout. `send` gets teleop / teleop_release messages; `onStop` the operator stop.
 */
export function TeleopPanel(props: {
  send: (msg: TeleopMessage) => void;
  onStop: () => void;
  maxSteer: number;
  maxSpeed: number;
  disabled?: boolean;
  state?: string;
}) {
  const { t } = useTranslation();
  const [armed, setArmed] = useState(false);
  const [limit, setLimit] = useState(props.maxSpeed);
  const { sample, gamepad, setTouch } = useTeleop({
    armed: armed && !props.disabled,
    limits: { maxSteer: props.maxSteer, speedLimit: limit },
    send: props.send,
    onStop: props.onStop,
  });
  return (
    <div className="panel" data-testid="teleop-panel">
      <h4 style={{ marginTop: 0 }}>{t("teleop.title")}</h4>
      <button className="stop-button" data-testid="teleop-stop" onClick={props.onStop}
              style={{ width: "100%", background: "#c62828", color: "#fff", fontWeight: 700, padding: "10px 0" }}>
        {t("teleop.stop")}
      </button>
      <label style={{ display: "block", marginTop: 8 }}>
        <input type="checkbox" checked={armed} disabled={props.disabled} onChange={(e) => setArmed(e.target.checked)}
               data-testid="teleop-arm" /> {t("teleop.arm")}
      </label>
      <div className="field" style={{ marginTop: 6 }}>
        <label htmlFor="teleop-limit">{t("teleop.limit", { v: limit.toFixed(2) })}</label>
        <input id="teleop-limit" data-testid="teleop-limit" type="range" min={0.05} max={props.maxSpeed} step={0.05}
               value={limit} onChange={(e) => setLimit(Number(e.target.value))} />
      </div>
      {armed && !props.disabled && <TouchJoystick onChange={setTouch} />}
      <dl className="kv" data-testid="teleop-readout">
        <dt>{t("teleop.source")}</dt><dd>{sample.engaged ? t(`teleop.src_${sample.source}`) : t("teleop.released")}</dd>
        <dt>{t("teleop.steer")}</dt><dd>{((sample.steer * 180) / Math.PI).toFixed(0)}°</dd>
        <dt>{t("teleop.speed")}</dt><dd data-testid="teleop-speed">{sample.speed.toFixed(2)} m/s</dd>
        {props.state && <><dt>{t("teleop.car_state")}</dt><dd data-testid="teleop-state">{props.state}</dd></>}
        <dt>{t("teleop.gamepad")}</dt><dd>{gamepad ? t("teleop.connected") : "–"}</dd>
      </dl>
      <p className="muted" style={{ marginBottom: 0, fontSize: 12 }}>{t("teleop.help")}</p>
    </div>
  );
}
