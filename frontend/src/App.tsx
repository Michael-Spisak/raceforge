import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type Health } from "./api/client";
import { ConstructScreen } from "./screens/ConstructScreen";
import { LiveScreen } from "./screens/LiveScreen";
import { PartsScreen } from "./screens/PartsScreen";
import { ReplayScreen } from "./screens/ReplayScreen";
import { ScansScreen } from "./screens/ScansScreen";
import { SimulateScreen } from "./screens/SimulateScreen";
import { StartScreen } from "./screens/StartScreen";
import { TeamScreen } from "./screens/TeamScreen";
import { type NavPreset, type Units, useSettings } from "./store/settings";
import { badgeOf, useWorkspace } from "./store/workspace";

export type Tab = "start" | "parts" | "construct" | "simulate" | "live" | "replay" | "scans" | "team";
const TABS: Tab[] = ["start", "parts", "construct", "simulate", "live", "replay", "scans", "team"];

export function App() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("start");
  const [health, setHealth] = useState<Health | null>(null);
  const [offline, setOffline] = useState(false);
  const { navPreset, setNavPreset, units, setUnits, language, setLanguage } = useSettings();
  const wsBadge = badgeOf(useWorkspace((s) => s.status));
  const refreshWorkspace = useWorkspace((s) => s.refresh);

  useEffect(() => {
    void refreshWorkspace(true);
    const id = setInterval(() => void refreshWorkspace(true), 15000);
    return () => clearInterval(id);
  }, [refreshWorkspace]);

  useEffect(() => {
    const poll = () => api.health().then((h) => { setHealth(h); setOffline(false); }).catch(() => setOffline(true));
    void poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">{t("app.title")}</span>
        <nav aria-label="main">
          {TABS.map((x) => (
            <button key={x} aria-current={tab === x ? "page" : undefined} onClick={() => setTab(x)} data-testid={`tab-${x}`}>
              {t(`nav.${x}`)}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        <select aria-label={t("settings.nav")} value={navPreset} onChange={(e) => setNavPreset(e.target.value as NavPreset)}>
          <option value="blender">Blender</option>
          <option value="studio">Studio</option>
          <option value="fusion">Fusion</option>
        </select>
        <select aria-label={t("settings.units")} value={units} onChange={(e) => setUnits(e.target.value as Units)}>
          <option value="mm">mm</option>
          <option value="studs">studs</option>
        </select>
        <select aria-label={t("settings.language")} value={language} onChange={(e) => setLanguage(e.target.value as "de" | "en")}>
          <option value="de">DE</option>
          <option value="en">EN</option>
        </select>
        <button className={`badge ${wsBadge === "online" ? "ok" : wsBadge === "offline" ? "bad" : ""}`}
                data-testid="workspace-status" onClick={() => setTab("team")}>{t(`team.badge_${wsBadge}`)}</button>
        <span className={`badge ${offline ? "bad" : "ok"}`} data-testid="engine-status">{offline ? t("engine.offline") : t("engine.online")}</span>
      </header>
      {tab === "start" && <StartScreen health={health} onOpen={setTab} />}
      {tab === "parts" && <PartsScreen />}
      {tab === "construct" && <ConstructScreen />}
      {tab === "simulate" && <SimulateScreen />}
      {tab === "live" && <LiveScreen />}
      {tab === "replay" && <ReplayScreen />}
      {tab === "scans" && <ScansScreen />}
      {tab === "team" && <TeamScreen />}
    </div>
  );
}
