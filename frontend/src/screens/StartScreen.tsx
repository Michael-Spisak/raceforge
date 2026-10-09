import { useTranslation } from "react-i18next";
import type { Health } from "../api/client";
import type { Tab } from "../App";

export function StartScreen({ health, onOpen }: { health: Health | null; onOpen: (tab: Tab) => void }) {
  const { t } = useTranslation();
  const cards: { tab: Tab; text: string }[] = [
    { tab: "parts", text: t("start.parts") },
    { tab: "construct", text: t("start.construct") },
    { tab: "simulate", text: t("start.simulate") },
    { tab: "train", text: t("start.train") },
    { tab: "live", text: t("start.live") },
    { tab: "replay", text: t("start.replay") },
    { tab: "scans", text: t("start.scans") },
  ];
  return (
    <div style={{ overflow: "auto" }}>
      <div style={{ padding: "24px 24px 0" }}>
        <h1 style={{ margin: 0 }}>{t("app.title")}</h1>
        <p className="muted">{t("start.intro")}</p>
        {health && !health.ldraw_available && <p className="warning">{t("engine.ldraw_missing")}</p>}
      </div>
      <div className="start-grid">
        {cards.map((c) => (
          <button key={c.tab} onClick={() => onOpen(c.tab)} data-testid={`start-${c.tab}`}>
            <h3>{t(`nav.${c.tab}`)}</h3>
            <span className="muted">{c.text}</span>
          </button>
        ))}
      </div>
      {health && <p className="muted" style={{ padding: "0 24px" }}>v{health.version}</p>}
    </div>
  );
}
