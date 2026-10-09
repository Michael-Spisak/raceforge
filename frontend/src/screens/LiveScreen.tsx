import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, type CarPairingCode, type Schemas, workspace } from "../api/client";
import { Dashboard } from "../live/Dashboard";
import { type Deployed, DeployPanel } from "../live/DeployPanel";
import { RaceChecklist } from "../live/RaceChecklist";
import { LATENCY_WARN_MS, useCarLink } from "../live/useCarLink";
import { useWorkspace } from "../store/workspace";
import { TeleopPanel } from "../teleop/TeleopPanel";

/** Car defaults (quick-start car): steering lock 30°, teleop speed slider up to 2 m/s. */
const MAX_STEER_RAD = (30 * Math.PI) / 180;
const MAX_SPEED = 2.0;

const fmt = (v: number | null | undefined, unit: string, digits = 2) => (v == null ? "–" : `${v.toFixed(digits)} ${unit}`);

/** Minimal live view of a real car in test mode (spec 0010 B): connection, status, teleop, events. */
export function LiveScreen() {
  const { t } = useTranslation();
  const link = useCarLink();
  const [url, setUrl] = useState(() => localStorage.getItem("rf.car.url") ?? "ws://raceforge-car.local:8765");
  const [token, setToken] = useState(() => sessionStorage.getItem("rf.car.token") ?? "");
  const [note, setNote] = useState("");
  const [phoneCode, setPhoneCode] = useState<CarPairingCode | null>(null);
  const [phoneError, setPhoneError] = useState("");
  const loggedIn = !!useWorkspace((st) => st.status?.logged_in && st.status.workspace);
  const [share, setShare] = useState(() => localStorage.getItem("rf.live.share") !== "0");
  const [shareRate, setShareRate] = useState(() => Number(localStorage.getItem("rf.live.share_rate") ?? 10));
  const [sessions, setSessions] = useState<Schemas["LiveSession"][]>([]);
  const [deployed, setDeployed] = useState<Deployed | null>(null);
  useEffect(() => {
    if (!loggedIn) return;
    const load = () => void workspace.liveSessions().then(setSessions).catch(() => setSessions([]));
    load();
    const id = window.setInterval(load, 5000);
    return () => window.clearInterval(id);
  }, [loggedIn]);
  const pairPhone = () => {
    setPhoneError("");
    api.carPairingCode(url, token).then(setPhoneCode)
      .catch((e: unknown) => setPhoneError(e instanceof ApiError ? e.message : String(e)));
  };

  const connect = (e: FormEvent) => {
    e.preventDefault();
    localStorage.setItem("rf.car.url", url);
    sessionStorage.setItem("rf.car.token", token);
    localStorage.setItem("rf.live.share", share ? "1" : "0");
    localStorage.setItem("rf.live.share_rate", String(shareRate));
    link.connect(url, token, share, shareRate);
  };
  const watching = link.watching != null;
  const connected = link.state === "connected" && !watching;
  const f = link.frame;
  const race = (f?.mode ?? link.car?.mode) === "race";
  const slow = link.rtt != null && link.rtt > LATENCY_WARN_MS;

  return (
    <div className="screen">
      <aside className="side">
        <form onSubmit={connect} className="panel">
          <h3 style={{ marginTop: 0 }}>{t("live.car")}</h3>
          <div className="field">
            <label htmlFor="car-url">{t("live.address")}</label>
            <input id="car-url" data-testid="car-url" value={url} onChange={(e) => setUrl(e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="car-token">{t("live.token")}</label>
            <input id="car-token" data-testid="car-token" type="password" value={token} onChange={(e) => setToken(e.target.value)} />
          </div>
          {loggedIn && (
            <div className="field">
              <label>
                <input type="checkbox" checked={share} data-testid="car-share" onChange={(e) => setShare(e.target.checked)} /> {t("live.share")}
              </label>
              {share && (
                <label style={{ display: "block" }}>
                  {t("live.share_rate")}{" "}
                  <input type="number" min={1} max={20} style={{ width: 60 }} value={shareRate} onChange={(e) => setShareRate(Number(e.target.value))} /> Hz
                </label>
              )}
            </div>
          )}
          <div style={{ display: "flex", gap: 6 }}>
            <button type="submit" className="primary" data-testid="car-connect">{t("live.connect")}</button>
            {(connected || link.state === "connecting") && <button type="button" onClick={link.disconnect}>{t("live.disconnect")}</button>}
          </div>
          <button type="button" onClick={pairPhone} data-testid="car-pair-phone" style={{ marginTop: 8 }}>{t("live.pair_phone")}</button>
          {phoneCode && (
            <div style={{ marginTop: 8 }}>
              <img data-testid="car-pair-qr" alt={t("live.pair_phone")} width={220} height={220} style={{ background: "#fff", padding: 8 }}
                   src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(phoneCode.qr_svg)}`} />
              <p className="muted">{t("live.pair_phone_hint")}</p>
            </div>
          )}
          {phoneError && <p className="error">{phoneError}</p>}
          <p data-testid="car-link-state" className={link.state === "error" ? "error" : "muted"}>
            {t(`live.state_${link.state}`)}{link.detail && link.state !== "connected" ? ` — ${link.detail}` : ""}
          </p>
          {!watching && link.state !== "idle" && loggedIn && (
            <p className="muted" data-testid="car-share-state">
              {t(`live.share_${link.share.state}`)}
              {link.share.run && ` · ${t("live.run_saved", { run: link.share.run })}`}
            </p>
          )}
        </form>
        {loggedIn && (
          <div className="panel" data-testid="team-live">
            <h4 style={{ marginTop: 0 }}>{t("live.team_live")}</h4>
            {sessions.length === 0 && <p className="muted">{t("live.team_none")}</p>}
            <ul className="list">
              {sessions.map((s) => (
                <li key={s.id}>
                  {s.car} <span className="muted">· {s.publisher}</span>{" "}
                  {link.watching === s.id
                    ? <button type="button" onClick={link.disconnect}>{t("live.stop_watching")}</button>
                    : <button type="button" onClick={() => link.watch(s.id)}>{t("live.watch")}</button>}
                </li>
              ))}
            </ul>
            {watching && <p className="muted">{t("live.watching", { car: link.car?.name ?? "", who: link.detail })}</p>}
          </div>
        )}
        {connected && (
          <TeleopPanel
            send={(msg) => link.send(msg)}
            onStop={() => link.send({ type: "stop", reason: "operator" })}
            maxSteer={MAX_STEER_RAD}
            maxSpeed={MAX_SPEED}
            disabled={race}
            state={f?.state}
          />
        )}
        {race && <p className="warning">{t("live.race_mode")}</p>}
        {connected && (
          <form className="field" onSubmit={(e) => { e.preventDefault(); link.send({ type: "note", text: note }); setNote(""); }}>
            <label htmlFor="car-note">{t("live.note")}</label>
            <div style={{ display: "flex", gap: 6 }}>
              <input id="car-note" value={note} onChange={(e) => setNote(e.target.value)} />
              <button type="submit" disabled={!note}>{t("live.add_note")}</button>
            </div>
          </form>
        )}
        <DeployPanel onInstalled={setDeployed} />
      </aside>
      <section className="main" style={{ display: "block", overflow: "auto", padding: 12 }}>
        <div className="panel" data-testid="car-status">
          <h3 style={{ marginTop: 0 }}>{link.car ? link.car.name : t("live.no_car")}</h3>
          <dl className="kv" style={{ maxWidth: 480 }}>
            <dt>{t("live.mode")}</dt><dd>{f?.mode ?? link.car?.mode ?? "–"}</dd>
            <dt>{t("live.state")}</dt><dd data-testid="car-state">{f?.state ?? "–"}</dd>
            <dt>{t("live.latency")}</dt>
            <dd data-testid="car-rtt" className={slow ? "error" : ""}>{fmt(link.rtt, "ms", 0)}{slow ? ` ⚠ ${t("live.slow")}` : ""}</dd>
            <dt>{t("live.cmd")}</dt>
            <dd>{f?.cmd ? `${((f.cmd.steering_rad * 180) / Math.PI).toFixed(0)}° · ${f.cmd.speed_m_s.toFixed(2)} m/s` : "–"}</dd>
            <dt>{t("live.speed")}</dt><dd>{fmt(f?.meas?.speed_m_s, "m/s")}</dd>
            <dt>{t("live.battery")}</dt><dd>{fmt(f?.power?.ev3_battery_v ?? f?.power?.motor_battery_v, "V", 1)}</dd>
            <dt>{t("live.loop")}</dt><dd>{f?.loop ? `${f.loop.rate_hz.toFixed(0)} Hz · ${fmt(f.loop.jitter_ms, "ms", 1)}` : "–"}</dd>
            <dt>{t("live.faults")}</dt><dd className={f?.faults?.length ? "error" : ""}>{f?.faults?.length ? f.faults.join(", ") : "–"}</dd>
          </dl>
        </div>
        {(link.state === "connected" || f) && (
          <Dashboard frame={f} lastFrameAt={link.lastFrameAt} rtt={watching ? null : link.rtt} onEvent={link.pushEvent} />
        )}
        {!watching && (
          <RaceChecklist connected={connected} frame={f} radio={link.radio} deployed={deployed}
                         runRadioCheck={() => link.send({ type: "radio_check" })}
                         saveNote={(text) => link.send({ type: "note", text })} />
        )}
        <div className="panel">
          <h4 style={{ marginTop: 0 }}>{t("live.events")}</h4>
          <ul className="list" data-testid="car-events">
            {link.events.map((e, i) => (
              <li key={i}><span className="muted">{new Date(e.at).toLocaleTimeString()}</span> {e.kind} {e.detail}</li>
            ))}
          </ul>
        </div>
      </section>
    </div>
  );
}
