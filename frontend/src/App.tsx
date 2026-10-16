import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type Health } from "./api/client";
import { ConstructScreen } from "./screens/ConstructScreen";
import { LiveScreen } from "./screens/LiveScreen";
import { PartsScreen } from "./screens/PartsScreen";
import { RaceControlScreen } from "./screens/RaceControlScreen";
import { ReplayScreen } from "./screens/ReplayScreen";
import { ScansScreen } from "./screens/ScansScreen";
import { SimulateScreen } from "./screens/SimulateScreen";
import { StartScreen } from "./screens/StartScreen";
import { TeamScreen } from "./screens/TeamScreen";
import { TracksScreen } from "./screens/TracksScreen";
import { TrainScreen } from "./screens/TrainScreen";
import { type ColourMode, type NavPreset, type Units, useSettings } from "./store/settings";
import { badgeOf, useWorkspace } from "./store/workspace";
import { AppMark, TabIcon } from "./ui/icons";

export type Tab = "start" | "parts" | "construct" | "tracks" | "simulate" | "train" | "live" | "race" | "replay" | "scans" | "team";
const TABS: Tab[] = ["start", "parts", "construct", "tracks", "simulate", "train", "live", "race", "replay", "scans", "team"];

export function App() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("start");
  const [health, setHealth] = useState<Health | null>(null);
  const [offline, setOffline] = useState(false);
  const { navPreset, setNavPreset, units, setUnits, language, setLanguage, colourMode, setColourMode } = useSettings();
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

  const led = offline ? "bad" : health ? "ok" : "warn";
  return (
    <div className="app">
      <aside className="rail">
        <div className="brand">
          <AppMark />
          <span>{t("app.title")}</span>
        </div>
        <nav aria-label="main" className="beam">
          {TABS.map((x) => (
            <button key={x} aria-current={tab === x ? "page" : undefined} onClick={() => setTab(x)} data-testid={`tab-${x}`}>
              <span className="hole" aria-hidden="true" />
              <TabIcon tab={x} />
              <span className="label">{t(`nav.${x}`)}</span>
            </button>
          ))}
        </nav>
      </aside>
      <div className="stage">
        <header className="topbar">
          <div className="lcd" role="status">
            <span className={`led ${led}`} aria-hidden="true" />
            <span className="lcd-main" data-testid="engine-status">{offline ? t("engine.offline") : t("engine.online")}</span>
            <span className="lcd-dim">{t(`nav.${tab}`)}</span>
            {health && <span className="lcd-dim">v{health.version}</span>}
          </div>
          <span className="spacer" />
          <div className="prefs">
            <select aria-label={t("settings.nav")} value={navPreset} onChange={(e) => setNavPreset(e.target.value as NavPreset)}>
              <option value="blender">Blender</option>
              <option value="studio">Studio</option>
              <option value="fusion">Fusion</option>
            </select>
            <select aria-label={t("settings.units")} value={units} onChange={(e) => setUnits(e.target.value as Units)}>
              <option value="mm">mm</option>
              <option value="studs">studs</option>
            </select>
            <select aria-label={t("settings.colours")} data-testid="colour-mode" value={colourMode} onChange={(e) => setColourMode(e.target.value as ColourMode)}>
              <option value="function">{t("settings.colours_function")}</option>
              <option value="real">{t("settings.colours_real")}</option>
            </select>
            <select aria-label={t("settings.language")} value={language} onChange={(e) => setLanguage(e.target.value as "de" | "en")}>
              <option value="de">DE</option>
              <option value="en">EN</option>
            </select>
            <button className={`badge ${wsBadge === "online" ? "ok" : wsBadge === "offline" ? "bad" : ""}`}
                    data-testid="workspace-status" onClick={() => setTab("team")}>{t(`team.badge_${wsBadge}`)}</button>
          </div>
        </header>
      {tab === "start" && <StartScreen health={health} onOpen={setTab} />}
      {tab === "parts" && <PartsScreen />}
      {tab === "construct" && <ConstructScreen />}
      {tab === "tracks" && <TracksScreen />}
      {tab === "simulate" && <SimulateScreen />}
      {tab === "train" && <TrainScreen />}
      {tab === "live" && <LiveScreen />}
      {tab === "race" && <RaceControlScreen />}
      {tab === "replay" && <ReplayScreen />}
      {tab === "scans" && <ScansScreen />}
      {tab === "team" && <TeamScreen />}
      </div>
    </div>
  );
}
