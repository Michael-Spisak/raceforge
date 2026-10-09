import { useState } from "react";
import { useTranslation } from "react-i18next";
import { loadThresholds } from "./dashboard";
import type { Deployed } from "./DeployPanel";
import type { CarFrame, RadioCheck } from "./useCarLink";

const MANUAL = ["batteries", "estop", "start_mode", "spare_car", "calibration"] as const;

interface Props {
  connected: boolean;
  frame: CarFrame | null;
  radio: RadioCheck | null;
  deployed: Deployed | null;
  runRadioCheck: () => void;
  saveNote: (text: string) => void;
}

interface Item {
  key: string;
  ok: boolean | null;
  detail?: string;
  manual?: boolean;
}

const mark = (ok: boolean | null) => (ok === true ? "✓" : ok === false ? "✗" : "?");

/** Guided race-day checklist (spec 0030, PLAN §5c): automatic checks + manual ticks, saved as a note. */
export function RaceChecklist({ connected, frame, radio, deployed, runRadioCheck, saveNote }: Props) {
  const { t } = useTranslation();
  const [ticked, setTicked] = useState<Record<string, boolean>>({});
  const [saved, setSaved] = useState(false);
  const minV = loadThresholds().minBatteryV;
  const volts = frame?.power?.ev3_battery_v ?? frame?.power?.motor_battery_v ?? null;
  const sensors = Object.keys(frame?.meas?.sensors ?? {}).length;
  const items: Item[] = [
    { key: "connected", ok: connected },
    { key: "battery", ok: volts == null ? null : volts >= minV, detail: volts == null ? "" : `${volts.toFixed(1)} V` },
    { key: "sensors", ok: frame ? !frame.faults?.length && sensors > 0 : null, detail: frame?.faults?.join(", ") ?? "" },
    { key: "radios", ok: radio ? radio.ok : null, detail: radio && !radio.ok ? radio.violations.join("; ") : "" },
    { key: "bundle", ok: deployed ? deployed.result.ok : null, detail: deployed ? `${deployed.bundle.name} · ${deployed.bundle.digest.slice(0, 12)}` : "" },
    { key: "race_bundle", ok: deployed ? deployed.bundle.mode === "race" : null },
    ...MANUAL.map((key) => ({ key, ok: !!ticked[key], manual: true })),
  ];
  const allOk = items.every((i) => i.ok === true);

  const save = () => {
    const line = items.map((i) => `${mark(i.ok)} ${t(`race.item_${i.key}`)}`).join(", ");
    saveNote(`race-day checklist: ${allOk ? "ALL OK" : "INCOMPLETE"} - ${line}`);
    setSaved(true);
  };

  return (
    <div className="panel" data-testid="race-checklist">
      <h4 style={{ marginTop: 0 }}>{t("race.title")}</h4>
      <ul className="list">
        {items.map((i) => (
          <li key={i.key}>
            {i.manual ? (
              <label>
                <input type="checkbox" checked={!!ticked[i.key]} onChange={(e) => setTicked({ ...ticked, [i.key]: e.target.checked })} /> {t(`race.item_${i.key}`)}
              </label>
            ) : (
              <span>
                <span style={{ color: i.ok === true ? "#2a9d55" : i.ok === false ? "#d33" : "#888" }}>{mark(i.ok)}</span>{" "}
                {t(`race.item_${i.key}`)} {i.detail && <span className="muted">· {i.detail}</span>}
              </span>
            )}
          </li>
        ))}
      </ul>
      {radio?.unsupported && <p className="warning">{t("race.radio_unsupported")}</p>}
      {deployed && deployed.bundle.mode !== "race" && <p className="warning">{t("race.not_race_bundle")}</p>}
      <div style={{ display: "flex", gap: 6 }}>
        <button type="button" onClick={runRadioCheck} disabled={!connected} data-testid="race-radio-check">{t("race.radio_check")}</button>
        <button type="button" onClick={save} disabled={!connected}>{t("race.save")}</button>
      </div>
      {allOk && <p data-testid="race-ready">{t("race.ready")}</p>}
      {saved && <p className="muted">{t("race.saved")}</p>}
      <p className="muted">{t("race.hint")}</p>
    </div>
  );
}
