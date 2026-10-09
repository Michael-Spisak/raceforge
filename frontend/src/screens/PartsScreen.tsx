import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type PartSummary } from "../api/client";
import { CarModel } from "../three/CarModel";
import { Viewport } from "../three/Viewport";

const CATEGORIES = ["", "beam", "axle", "pin", "gear", "differential", "steering_arm", "wheel_rim", "tyre",
  "ev3_brick", "motor", "sensor", "board", "battery"];

export function PartsScreen() {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [parts, setParts] = useState<PartSummary[]>([]);
  const [selected, setSelected] = useState<PartSummary | null>(null);

  useEffect(() => {
    const id = setTimeout(() => void api.parts(query, category).then(setParts), 150);
    return () => clearTimeout(id);
  }, [query, category]);

  const preview = selected && {
    name: selected.key,
    bodies: [{ name: "part", parts: [{ key: selected.key, ldraw_id: selected.ldraw_id, category: selected.category,
      pos: [0, 0, 0] as [number, number, number], quat: [1, 0, 0, 0] as [number, number, number, number], color: 72, real_color: selected.color ?? 72,
      bbox_lo: [-0.01, -0.01, -0.01] as [number, number, number], bbox_hi: [0.01, 0.01, 0.01] as [number, number, number] }] }],
  };

  return (
    <div className="screen">
      <aside className="side">
        <div className="field">
          <label htmlFor="q">{t("parts.search")}</label>
          <input id="q" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="cat">{t("parts.category")}</label>
          <select id="cat" value={category} onChange={(e) => setCategory(e.target.value)}>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>{c || t("parts.all")}</option>
            ))}
          </select>
        </div>
        <ul className="list" role="listbox" data-testid="parts-list">
          {parts.map((p) => (
            <li key={p.key} role="option" aria-selected={selected?.key === p.key} onClick={() => setSelected(p)}>
              <div>{p.name}</div>
              <div className="muted">{p.ldraw_id ?? p.key} · {p.category}</div>
            </li>
          ))}
        </ul>
      </aside>
      <section className="main">
        {preview ? (
          <Viewport camera={[0.12, -0.12, 0.1]} target={[0, 0, 0]}>
            <CarModel car={preview} />
          </Viewport>
        ) : <div />}
        {selected && (
          <div className="statusbar">
            <strong>{selected.name}</strong>
            <span>{t("parts.mass")}: {selected.mass_g} g</span>
            <span>{t("parts.connectors")}: {selected.connectors}</span>
            {selected.device && <span>{t("parts.device")}: {selected.device}</span>}
          </div>
        )}
      </section>
    </div>
  );
}
