import { useTranslation } from "react-i18next";
import type { Health } from "../api/client";
import type { Tab } from "../App";

export function StartScreen({ health, onOpen }: { health: Health | null; onOpen: (tab: Tab) => void }) {
  const { t } = useTranslation();
  const cards: { tab: Tab; text: string }[] = [
    { tab: "parts", text: t("start.parts") },
    { tab: "construct", text: t("start.construct") },
    { tab: "tracks", text: t("start.tracks") },
    { tab: "simulate", text: t("start.simulate") },
    { tab: "train", text: t("start.train") },
    { tab: "live", text: t("start.live") },
    { tab: "replay", text: t("start.replay") },
    { tab: "scans", text: t("start.scans") },
  ];
  return (
    <div className="start">
      <div className="start-inner">
        <div className={`statebar ${health ? "ok" : "warn"}`}>
          <span className="sb-t">{health ? t("engine.online") : t("engine.offline")}</span>
          <span className="sb-d">{t("start.intro")}</span>
        </div>
        {health && !health.ldraw_available && <div className="note warn">{t("engine.ldraw_missing")}</div>}
        <div className="start-grid">
          {cards.map((c) => (
            <button key={c.tab} onClick={() => onOpen(c.tab)} data-testid={`start-${c.tab}`}>
              <h3>{t(`nav.${c.tab}`)}</h3>
              <span className="muted">{c.text}</span>
            </button>
          ))}
        </div>
        {health && <p className="hint">v{health.version}</p>}
      </div>
    </div>
  );
}
