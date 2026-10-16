import { useTranslation } from "react-i18next";
import type { Health } from "../api/client";
import type { Tab } from "../App";

/** The workflow in driving order, shown like a timing tower: position, step, what it does. */
const STEPS: Tab[] = ["parts", "construct", "tracks", "simulate", "train", "live", "replay"];
const MORE: Tab[] = ["scans", "race", "team"];

export function StartScreen({ health, onOpen }: { health: Health | null; onOpen: (tab: Tab) => void }) {
  const { t } = useTranslation();
  return (
    <div className="start">
      <div className="start-inner">
        <div className="start-head">
          <div>
            <h1>{t("app.title")}</h1>
            <p>{t("start.intro")}</p>
          </div>
          {health && <span className="ver num muted">v{health.version}</span>}
        </div>
        {health && !health.ldraw_available && <div className="notice" role="status">{t("engine.ldraw_missing")}</div>}
        <div className="tower">
          {STEPS.map((tab, i) => (
            <button key={tab} type="button" className="tower-row" onClick={() => onOpen(tab)} data-testid={`start-${tab}`}>
              <span className="pos">{String(i + 1).padStart(2, "0")}</span>
              <span className="name">{t(`nav.${tab}`)}</span>
              <span className="what">{t(`start.${tab}`)}</span>
            </button>
          ))}
        </div>
        <div className="aside-links">
          {MORE.map((tab) => (
            <button key={tab} type="button" onClick={() => onOpen(tab)} data-testid={`start-${tab}`}>{t(`nav.${tab}`)}</button>
          ))}
        </div>
      </div>
    </div>
  );
}
