import { create } from "zustand";
import i18n, { type Language, systemLanguage } from "../i18n";

export type NavPreset = "blender" | "studio" | "fusion";
export type Units = "mm" | "studs";

interface SettingsState {
  navPreset: NavPreset;
  units: Units;
  language: Language;
  setNavPreset: (p: NavPreset) => void;
  setUnits: (u: Units) => void;
  setLanguage: (l: Language) => void;
}

function load<T extends string>(key: string, fallback: T, allowed: readonly T[]): T {
  try {
    const v = localStorage.getItem(`raceforge.${key}`);
    return v && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
  } catch {
    return fallback;
  }
}

function save(key: string, value: string): void {
  try {
    localStorage.setItem(`raceforge.${key}`, value);
  } catch {
    /* storage unavailable: setting stays for this session */
  }
}

export const useSettings = create<SettingsState>((set) => ({
  navPreset: load<NavPreset>("navPreset", "blender", ["blender", "studio", "fusion"]),
  units: load<Units>("units", "mm", ["mm", "studs"]),
  language: load<Language>("language", systemLanguage(), ["de", "en"]),
  setNavPreset: (navPreset) => {
    save("navPreset", navPreset);
    set({ navPreset });
  },
  setUnits: (units) => {
    save("units", units);
    set({ units });
  },
  setLanguage: (language) => {
    save("language", language);
    void i18n.changeLanguage(language);
    set({ language });
  },
}));

/** Formats a length in metres for display (mm or LEGO studs, 1 stud = 8 mm). */
export function formatLength(metres: number, units: Units): string {
  return units === "studs" ? `${(metres / 0.008).toFixed(1)} studs` : `${(metres * 1000).toFixed(0)} mm`;
}
