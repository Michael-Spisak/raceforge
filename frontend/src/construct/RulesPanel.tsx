import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, type Schemas } from "../api/client";

type RuleCheck = Schemas["RuleCheck"];
type BudgetView = Schemas["BudgetView"];
type Settings = Schemas["ConstructSettings"];

const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

function ruleText(t: (k: string, o?: Record<string, unknown>) => string, r: RuleCheck): string {
  const p = r.params as Record<string, string | number>;
  const fmt: Record<string, unknown> = { ...p };
  if (r.id.startsWith("max_")) { // m and kg from the engine, shown as mm and g
    fmt.value = Math.round(Number(p.value) * 1000);
    if (p.limit != null) fmt.limit = Math.round(Number(p.limit) * 1000);
  }
  const state = r.ok === null ? "unset" : r.ok ? "ok" : "fail";
  return t(`rules.${r.id}_${state}`, fmt);
}

/** Live rule checker + budget + limits for the Construct editor (spec 0016). */
export function RulesPanel({ rules, budget, showOverlaps, onShowOverlaps, onSelect, onSettingsSaved }: {
  rules: RuleCheck[];
  budget: BudgetView | null | undefined;
  showOverlaps: boolean;
  onShowOverlaps: (v: boolean) => void;
  onSelect: (path: string[]) => void;
  onSettingsSaved: () => void;
}) {
  const { t } = useTranslation();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState("");
  useEffect(() => void api.constructSettings().then(setSettings).catch((e: unknown) => setError(message(e))), []);

  const save = (next: Settings) => {
    setSettings(next);
    setError("");
    api.saveConstructSettings(next).then(() => onSettingsSaved()).catch((e: unknown) => setError(message(e)));
  };
  const setPrice = (key: string, eur: string) => {
    if (!settings) return;
    const rest = Object.fromEntries(Object.entries(settings.prices).filter(([k]) => k !== key));
    const prices = eur === "" ? rest
      : { ...rest, [key]: { eur: Number(eur), link: settings.prices[key]?.link ?? "", date: new Date().toISOString().slice(0, 10) } };
    save({ ...settings, prices });
  };
  const setLimit = (key: keyof Settings["limits"], v: string, scale: number) => {
    if (!settings) return;
    save({ ...settings, limits: { ...settings.limits, [key]: v === "" ? null : Number(v) / scale } });
  };
  const limitInput = (key: keyof Settings["limits"], label: string, scale: number) => (
    <div className="field" style={{ flex: 1, minWidth: 90 }}>
      <label htmlFor={`lim-${key}`}>{label}</label>
      <input id={`lim-${key}`} type="number" min={0} defaultValue={settings?.limits[key] != null ? Math.round((settings.limits[key] ?? 0) * scale) : ""}
             onBlur={(e) => setLimit(key, e.target.value, scale)} />
    </div>
  );

  return (
    <div className="panel" data-testid="editor-rules">
      <h4 style={{ marginTop: 0 }}>{t("rules.title")}</h4>
      <ul className="list">
        {rules.map((r) => (
          <li key={r.id} data-testid={`rule-${r.id}`} className={r.ok === false ? "error" : r.ok === null ? "muted" : ""}
              onClick={() => r.paths[0] && onSelect(r.paths[0])}>
            {r.ok === null ? "–" : r.ok ? "✓" : "✗"} {ruleText(t, r)}
          </li>
        ))}
      </ul>
      <label><input type="checkbox" checked={showOverlaps} onChange={(e) => onShowOverlaps(e.target.checked)} data-testid="show-overlaps" /> {t("rules.show_overlaps")}</label>

      {budget && (
        <>
          <h4>{t("rules.budget", { total: budget.total_eur.toFixed(2), limit: budget.limit_eur })}</h4>
          <table className="table" data-testid="editor-budget">
            <thead><tr><th>{t("rules.part")}</th><th>×</th><th>€</th></tr></thead>
            <tbody>
              {budget.items.map((i) => (
                <tr key={i.key}>
                  <td title={i.key}>{i.name}</td>
                  <td>{i.count}</td>
                  <td>
                    <input type="number" min={0} step={0.01} style={{ width: 70 }} placeholder={i.unit_eur != null ? i.unit_eur.toFixed(2) : t("rules.price_missing")}
                           defaultValue={settings?.prices[i.key]?.eur ?? (i.unit_eur === 0 ? "" : i.unit_eur ?? "")}
                           className={i.unit_eur == null ? "error" : ""}
                           onBlur={(e) => setPrice(i.key, e.target.value)} />
                    {i.stale && <span className="warning" title={t("rules.stale")}> ⏱</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">{t("rules.price_hint")}</p>
        </>
      )}
      {settings && (
        <>
          <h4>{t("rules.limits")}</h4>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {limitInput("max_length_m", t("rules.max_length_mm"), 1000)}
            {limitInput("max_width_m", t("rules.max_width_mm"), 1000)}
            {limitInput("max_height_m", t("rules.max_height_mm"), 1000)}
            {limitInput("max_mass_kg", t("rules.max_mass_g"), 1000)}
          </div>
          <div className="field">
            <label htmlFor="lim-filament">{t("rules.filament_price")}</label>
            <input id="lim-filament" type="number" min={0} step={0.5} defaultValue={settings.filament_eur_per_kg}
                   onBlur={(e) => e.target.value && save({ ...settings, filament_eur_per_kg: Number(e.target.value) })} />
          </div>
          <div className="field">
            <label htmlFor="lim-budget">{t("rules.budget_limit")}</label>
            <input id="lim-budget" type="number" min={1} defaultValue={settings.budget_eur}
                   onBlur={(e) => e.target.value && save({ ...settings, budget_eur: Number(e.target.value) })} />
          </div>
        </>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}
