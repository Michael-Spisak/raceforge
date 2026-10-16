import { useTranslation } from "react-i18next";
import type { Health } from "../api/client";
import type { Tab } from "../App";
import { TabIcon } from "../ui/icons";

/** EV3 programming palette: build = action (green), world = sensor (yellow), loops = flow (orange), logs = data (red). */
type Cat = "action" | "sensor" | "flow" | "data";
const PROGRAM: { tab: Tab; cat: Cat }[] = [
  { tab: "parts", cat: "action" },
  { tab: "construct", cat: "action" },
  { tab: "tracks", cat: "sensor" },
  { tab: "scans", cat: "sensor" },
  { tab: "simulate", cat: "flow" },
  { tab: "train", cat: "flow" },
  { tab: "live", cat: "action" },
  { tab: "replay", cat: "data" },
];
const CATS: Cat[] = ["action", "sensor", "flow", "data"];

/** A stylised EV3 brick; the display shows the engine state and the centre button is the status light. */
function Ev3Brick({ health, offline }: { health: Health | null; offline: boolean }) {
  const { t } = useTranslation();
  const led = offline ? "bad" : health ? "ok" : "warn";
  return (
    <svg className="ev3" viewBox="0 0 260 340" role="img" aria-label={offline ? t("engine.offline") : t("engine.online")}>
      {/* ports A-D on top, 1-4 at the bottom */}
      {["A", "B", "C", "D"].map((p, i) => (
        <g key={p}>
          <rect x={34 + i * 50} y="0" width="42" height="18" rx="3" className="ev3-port" />
          <text x={55 + i * 50} y="13" className="ev3-portlabel">{p}</text>
        </g>
      ))}
      {["1", "2", "3", "4"].map((p, i) => (
        <g key={p}>
          <rect x={34 + i * 50} y="322" width="42" height="18" rx="3" className="ev3-port" />
          <text x={55 + i * 50} y="335" className="ev3-portlabel">{p}</text>
        </g>
      ))}
      <rect x="6" y="14" width="248" height="312" rx="26" className="ev3-shell" />
      <rect x="18" y="26" width="224" height="288" rx="18" className="ev3-face" />
      {/* display */}
      <rect x="40" y="44" width="180" height="104" rx="6" className="ev3-bezel" />
      <rect x="48" y="52" width="164" height="88" rx="2" className="ev3-lcd" />
      <text x="58" y="76" className="ev3-lcdtext big">{t("app.title")}</text>
      <text x="58" y="100" className="ev3-lcdtext">{offline ? t("engine.offline") : t("engine.online")}</text>
      <text x="58" y="122" className="ev3-lcdtext small">{health ? `v${health.version}` : "..."}</text>
      <rect x="182" y="58" width="22" height="10" className="ev3-batt" />
      <rect x="204" y="61" width="3" height="4" className="ev3-batt" />
      {/* buttons: back, the cross, and the centre status light */}
      <path d="M40 168h34l-8 18H40z" className="ev3-btn" />
      <g transform="translate(130 222)">
        <path d="M-58 -16h30l8 16-8 16h-30z" className="ev3-btn" />
        <path d="M58 -16h-30l-8 16 8 16h30z" className="ev3-btn" />
        <path d="M-16 -58v30l16 8 16-8v-30z" className="ev3-btn" />
        <path d="M-16 58v-30l16-8 16 8v30z" className="ev3-btn" />
        <rect x="-19" y="-19" width="38" height="38" rx="6" className={`ev3-centre ${led}`} />
        <rect x="-12" y="-12" width="24" height="24" rx="4" className="ev3-centre-cap" />
      </g>
      {/* speaker grille */}
      {[0, 1, 2, 3].map((i) => <rect key={i} x={200} y={272 + i * 8} width="22" height="3" rx="1.5" className="ev3-grille" />)}
    </svg>
  );
}

export function StartScreen({ health, onOpen }: { health: Health | null; onOpen: (tab: Tab) => void }) {
  const { t } = useTranslation();
  const offline = health === null;
  return (
    <div className="start">
      <div className="start-inner">
        <section className="hero">
          <Ev3Brick health={health} offline={offline} />
          <div className="hero-text">
            <h1>{t("start.headline")}</h1>
            <p className="lead">{t("start.intro")}</p>
            {health && !health.ldraw_available && <div className="notice" role="status">{t("engine.ldraw_missing")}</div>}
            <div className="hero-actions">
              <button type="button" className="primary" onClick={() => onOpen("construct")}>{t("start.cta_build")}</button>
              <button type="button" onClick={() => onOpen("simulate")}>{t("start.cta_drive")}</button>
            </div>
          </div>
        </section>

        <section className="program" aria-labelledby="program-title">
          <div className="program-head">
            <h2 id="program-title">{t("start.program")}</h2>
            <ul className="cats">
              {CATS.map((c) => <li key={c}><span className={`swatch ${c}`} aria-hidden="true" />{t(`start.cat_${c}`)}</li>)}
            </ul>
          </div>
          <ol className="blocks">
            <li className="block start-block" aria-hidden="true">
              <span className="block-top"><svg viewBox="0 0 24 24" width="22" height="22"><path d="M8 5v14l11-7z" fill="currentColor" /></svg></span>
            </li>
            {PROGRAM.map(({ tab, cat }) => (
              <li key={tab} className={`block ${cat}`}>
                <button type="button" onClick={() => onOpen(tab)} data-testid={`start-${tab}`}>
                  <span className="block-top"><TabIcon tab={tab} /><span>{t(`nav.${tab}`)}</span></span>
                  <span className="block-body">{t(`start.${tab}`)}</span>
                </button>
              </li>
            ))}
          </ol>
          <p className="hint">{t("start.program_hint")}</p>
        </section>
      </div>
    </div>
  );
}
