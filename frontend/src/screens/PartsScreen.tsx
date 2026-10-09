import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, type PartSummary, type Schemas } from "../api/client";
import { CarModel } from "../three/CarModel";
import { Viewport } from "../three/Viewport";

type LDrawPart = Schemas["LDrawPart"];

const CATEGORIES = ["", "beam", "axle", "pin", "bush", "axle_joiner", "gear", "differential", "steering_arm", "steering_link",
  "cv_joint", "wheel_rim", "tyre", "ev3_brick", "motor", "sensor", "board", "battery"];
const LEGO_COLOURS: [number, string][] = [[0, "black"], [71, "light_grey"], [72, "dark_grey"], [15, "white"], [1, "blue"],
  [4, "red"], [14, "yellow"], [2, "green"], [25, "orange"], [19, "tan"]];

const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

function previewOf(key: string, ldrawId: string | null, category: string, colour: number) {
  return {
    name: key,
    bodies: [{ name: "part", parts: [{ key, ldraw_id: ldrawId, category,
      pos: [0, 0, 0] as [number, number, number], quat: [1, 0, 0, 0] as [number, number, number, number], color: colour, real_color: colour,
      bbox_lo: [-0.01, -0.01, -0.01] as [number, number, number], bbox_hi: [0.01, 0.01, 0.01] as [number, number, number] }] }],
  };
}

/** Category guess for an LDraw title (the user can change it before adding). */
function guessCategory(title: string): string {
  const t = title.toLowerCase();
  if (t.includes("tyre") || t.includes("tire")) return "tyre";
  if (t.includes("wheel")) return "wheel_rim";
  if (t.includes("gear")) return "gear";
  if (t.includes("pin")) return "pin";
  if (t.includes("axle")) return "axle";
  if (t.includes("bush")) return "bush";
  if (t.includes("connector") || t.includes("joiner")) return "axle_joiner";
  return "beam";
}

/** Catalogue browser; the whole LDraw library can be searched and parts added to the team's catalogue (spec 0018). */
export function PartsScreen() {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [library, setLibrary] = useState(false);
  const [parts, setParts] = useState<PartSummary[]>([]);
  const [ldraw, setLdraw] = useState<LDrawPart[]>([]);
  const [selected, setSelected] = useState<PartSummary | null>(null);
  const [candidate, setCandidate] = useState<LDrawPart | null>(null);
  const [form, setForm] = useState({ category: "beam", mass_g: 1, holes: "", length_studs: "", color: 0 });
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const id = setTimeout(() => {
      if (library) void api.ldrawParts(query).then(setLdraw).catch((e: unknown) => setError(message(e)));
      else void api.parts(query, category).then(setParts);
    }, 200);
    return () => clearTimeout(id);
  }, [query, category, library, reload]);

  const pickCandidate = (p: LDrawPart) => {
    setCandidate(p);
    setSelected(null);
    setNote("");
    setError("");
    setForm((f) => ({ ...f, category: guessCategory(p.title) }));
  };

  const add = () => {
    if (!candidate) return;
    setError("");
    api.addLocalPart({
      ldraw_id: candidate.ldraw_id, name: candidate.title, category: form.category, mass_g: form.mass_g,
      holes: form.holes ? Number(form.holes) : null, length_studs: form.length_studs ? Number(form.length_studs) : null, color: form.color,
    })
      .then((p) => { setNote(t("parts.added", { name: p.name })); setReload((n) => n + 1); })
      .catch((e: unknown) => setError(message(e)));
  };

  const preview = selected
    ? previewOf(selected.key, selected.ldraw_id, selected.category, selected.color ?? 72)
    : candidate ? previewOf(candidate.ldraw_id, candidate.ldraw_id, form.category, form.color) : null;

  return (
    <div className="screen">
      <aside className="side">
        <div className="field">
          <label><input type="checkbox" checked={library} onChange={(e) => setLibrary(e.target.checked)} data-testid="parts-library" /> {t("parts.whole_library")}</label>
        </div>
        <div className="field">
          <label htmlFor="q">{t("parts.search")}</label>
          <input id="q" data-testid="parts-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={library ? t("parts.library_hint") : ""} />
        </div>
        {!library && (
          <div className="field">
            <label htmlFor="cat">{t("parts.category")}</label>
            <select id="cat" value={category} onChange={(e) => setCategory(e.target.value)}>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{c ? t(`category.${c}`) : t("parts.all")}</option>
              ))}
            </select>
          </div>
        )}
        <ul className="list" role="listbox" data-testid="parts-list">
          {!library && parts.map((p) => (
            <li key={p.key} role="option" aria-selected={selected?.key === p.key} onClick={() => { setSelected(p); setCandidate(null); }}>
              <div>{p.name}{p.origin === "local" && <span className="badge" style={{ marginLeft: 6 }}>{t("parts.team")}</span>}</div>
              <div className="muted">{p.ldraw_id ?? p.key} · {t(`category.${p.category}`)}</div>
            </li>
          ))}
          {library && ldraw.map((p) => (
            <li key={p.ldraw_id} role="option" aria-selected={candidate?.ldraw_id === p.ldraw_id} onClick={() => pickCandidate(p)}>
              <div>{p.title}</div>
              <div className="muted">{p.ldraw_id} · {p.category}{p.in_catalogue ? ` · ${t("parts.in_catalogue")}` : ""}</div>
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
            {selected.origin === "local" && <span className="muted">{t("parts.unverified")}</span>}
          </div>
        )}
        {candidate && (
          <div className="statusbar" data-testid="parts-add-form" style={{ flexWrap: "wrap", gap: 8 }}>
            <strong style={{ width: "100%" }}>{candidate.title} ({candidate.ldraw_id})</strong>
            {candidate.in_catalogue ? <span className="muted">{t("parts.in_catalogue")}</span> : (
              <>
                <label>{t("parts.category")}{" "}
                  <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                    {CATEGORIES.filter(Boolean).map((c) => <option key={c} value={c}>{t(`category.${c}`)}</option>)}
                  </select>
                </label>
                <label>{t("parts.mass")} (g){" "}
                  <input type="number" min={0.01} step={0.01} style={{ width: 70 }} value={form.mass_g} onChange={(e) => setForm({ ...form, mass_g: Number(e.target.value) })} />
                </label>
                <label title={t("parts.holes_hint")}>{t("parts.holes")}{" "}
                  <input type="number" min={1} max={40} style={{ width: 55 }} value={form.holes} onChange={(e) => setForm({ ...form, holes: e.target.value })} />
                </label>
                <label title={t("parts.length_hint")}>{t("parts.length_studs")}{" "}
                  <input type="number" min={1} max={40} style={{ width: 55 }} value={form.length_studs} onChange={(e) => setForm({ ...form, length_studs: e.target.value })} />
                </label>
                <label>{t("parts.colour")}{" "}
                  <select value={form.color} onChange={(e) => setForm({ ...form, color: Number(e.target.value) })}>
                    {LEGO_COLOURS.map(([code, name]) => <option key={code} value={code}>{t(`colour.${name}`)}</option>)}
                  </select>
                </label>
                <button type="button" className="primary" onClick={add} data-testid="parts-add">{t("parts.add_to_catalogue")}</button>
              </>
            )}
            {note && <span>{note}</span>}
            {error && <span className="error" role="alert">{error}</span>}
          </div>
        )}
      </section>
    </div>
  );
}
