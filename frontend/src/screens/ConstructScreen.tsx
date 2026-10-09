import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, type QuickStartParams, type QuickstartResponse, type QuickstartSchema, versionLabel, workspace } from "../api/client";
import { useWorkspace } from "../store/workspace";
import { formatLength, useSettings } from "../store/settings";
import { AssemblyEditor } from "../construct/AssemblyEditor";
import { CarModel } from "../three/CarModel";
import { Viewport } from "../three/Viewport";

export type Draft = Partial<QuickStartParams> & { lidar?: boolean };

export const DEFAULT_SENSORS = [
  { kind: "ev3_ultrasonic", preset: "front" },
  { kind: "ev3_ultrasonic", preset: "left" },
  { kind: "ev3_ultrasonic", preset: "right" },
] as const;

/** Converts the form draft into request parameters (LiDAR checkbox -> sensor list). */
export function toParams(d: Draft): Partial<QuickStartParams> {
  const { lidar, ...rest } = d;
  const sensors = [...DEFAULT_SENSORS, ...(lidar ? [{ kind: "lidar_2d", preset: "top" } as const] : [])];
  return { ...rest, sensors: sensors.map((s) => ({ ...s, offset_mm: [0, 0, 0] })) as QuickStartParams["sensors"] };
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function SaveVersion({ draft }: { draft: Draft }) {
  const { t } = useTranslation();
  const status = useWorkspace((s) => s.status);
  const refresh = useWorkspace((s) => s.refresh);
  const [slug, setSlug] = useState("car-a");
  const [msg, setMsg] = useState("");
  const [note, setNote] = useState("");
  if (!status?.logged_in || !status.workspace) return <p className="muted">{t("construct.save_login")}</p>;
  const save = () => {
    setNote("");
    workspace.saveQuickstart(slug, toParams(draft), msg)
      .then((v) => {
        const label = versionLabel(v);
        setNote(label ? t("team.saved", { version: label }) : t("team.saved_offline"));
        setMsg("");
        void refresh();
      })
      .catch((e: unknown) => setNote(e instanceof ApiError ? e.message : String(e)));
  };
  return (
    <div>
      <div className="field">
        <label htmlFor="save-slug">{t("team.slug")} ({status.workspace.name})</label>
        <input id="save-slug" data-testid="save-slug" value={slug} onChange={(e) => setSlug(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="save-msg">{t("team.message")}</label>
        <input id="save-msg" data-testid="save-message" value={msg} onChange={(e) => setMsg(e.target.value)} />
      </div>
      <button data-testid="save-version" onClick={save}>{t("team.save_version")}</button>
      {note && <p data-testid="save-note">{note}</p>}
    </div>
  );
}

export function ConstructScreen() {
  const { t } = useTranslation();
  const units = useSettings((s) => s.units);
  const [schema, setSchema] = useState<QuickstartSchema | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [result, setResult] = useState<QuickstartResponse | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    void api.quickstartSchema().then((s) => {
      setSchema(s);
      setDraft({ ...(s.defaults as Draft), lidar: false });
    });
  }, []);

  useEffect(() => {
    if (!schema) return;
    setBusy(true);
    const id = setTimeout(() => {
      api.quickstart(toParams(draft))
        .then((r) => {
          setResult(r);
          setErrors({});
        })
        .catch((e: unknown) => setErrors(e instanceof ApiError ? { ...e.fieldErrors, _: e.message } : { _: String(e) }))
        .finally(() => setBusy(false));
    }, 250);
    return () => clearTimeout(id);
  }, [draft, schema]);

  if (!schema) return <p style={{ padding: 24 }}>{t("common.loading")}</p>;
  if (editing) return <AssemblyEditor start={editing} params={toParams(draft)} onExit={() => setEditing(null)} />;
  const opt = schema.options;
  const set = (patch: Draft) => setDraft((d) => ({ ...d, ...patch }));
  const gears = draft.differential === false ? opt["drive_gears_locked"] : opt["drive_gears_differential"];
  const derived = result?.derived as Record<string, number | null> | undefined;

  const select = (name: keyof Draft, label: string, values: unknown[] | undefined, numeric = false) => (
    <div className="field">
      <label htmlFor={name}>{label}</label>
      <select
        id={name}
        data-testid={`field-${name}`}
        value={String(draft[name] ?? "")}
        onChange={(e) => set({ [name]: numeric ? Number(e.target.value) : e.target.value } as Draft)}
      >
        {(values ?? []).map((v) => (
          <option key={String(v)} value={String(v)}>{String(v)}</option>
        ))}
      </select>
      {errors[name] && <span className="error" role="alert">{errors[name]}</span>}
    </div>
  );

  return (
    <div className="screen">
      <aside className="side">
        <h3>{t("construct.params")}</h3>
        {select("layout", t("construct.layout"), opt["layout"])}
        <div className="field">
          <label htmlFor="wheelbase_studs">{t("construct.wheelbase")}</label>
          <input id="wheelbase_studs" data-testid="field-wheelbase_studs" type="number"
                 value={draft.wheelbase_studs ?? ""} onChange={(e) => set({ wheelbase_studs: Number(e.target.value) })} />
          {errors["wheelbase_studs"] && <span className="error" role="alert">{errors["wheelbase_studs"]}</span>}
        </div>
        <div className="field">
          <label htmlFor="track_studs">{t("construct.track")}</label>
          <input id="track_studs" type="number" value={draft.track_studs ?? ""}
                 onChange={(e) => set({ track_studs: Number(e.target.value) })} />
          {errors["track_studs"] && <span className="error" role="alert">{errors["track_studs"]}</span>}
        </div>
        <div className="field">
          <label><input type="checkbox" checked={draft.differential ?? true}
                        onChange={(e) => set({ differential: e.target.checked, drive_gears: null })} /> {t("construct.differential")}</label>
        </div>
        {select("drive_motor", t("construct.drive_motor"), opt["drive_motor"])}
        {select("drive_gears", t("construct.drive_gears"), gears)}
        {select("steering_motor", t("construct.steering_motor"), opt["steering_motor"])}
        <div className="field">
          <label htmlFor="max_steer_deg">{t("construct.max_steer")}</label>
          <input id="max_steer_deg" type="number" value={draft.max_steer_deg ?? ""}
                 onChange={(e) => set({ max_steer_deg: Number(e.target.value) })} />
          {errors["max_steer_deg"] && <span className="error" role="alert">{errors["max_steer_deg"]}</span>}
        </div>
        {select("board", t("construct.board"), opt["board"])}
        <div className="field">
          <label><input type="checkbox" checked={draft.lidar ?? false} onChange={(e) => set({ lidar: e.target.checked })} /> {t("construct.lidar")}</label>
        </div>
        {errors["_"] && !Object.keys(errors).some((k) => k !== "_") && <p className="error" role="alert">{errors["_"]}</p>}
        <button type="button" className="primary" data-testid="construct-edit" disabled={!result}
                onClick={() => result && setEditing(result.assembly)}>{t("editor.open")}</button>
        <p className="muted">{t("editor.open_hint")}</p>
        <h3>{t("construct.export")}</h3>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {(["assembly", "mpd", "mjcf"] as const).map((k) => (
            <button key={k} onClick={() => void api.exportText(k, toParams(draft)).then((text) =>
              download({ assembly: "assembly.json", mpd: "car.mpd", mjcf: "car.xml" }[k], text))}>{k}</button>
          ))}
        </div>
        <h3>{t("construct.versions")}</h3>
        <SaveVersion draft={draft} />
      </aside>
      <section className="main">
        {result ? (
          <Viewport testId="construct-viewport">
            <CarModel car={result.car} />
            <gridHelper args={[1, 40, "#9aa1ab", "#c4c9d0"]} rotation-x={Math.PI / 2} />
          </Viewport>
        ) : <div />}
        <div className="statusbar" data-testid="derived">
          {busy && <span className="muted">{t("common.loading")}</span>}
          {derived && (
            <dl className="kv" style={{ gridTemplateColumns: "repeat(4, auto auto)", width: "100%" }}>
              <dt>{t("construct.mass")}</dt><dd data-testid="derived-mass">{((derived["mass_kg"] ?? 0) * 1000).toFixed(0)} g</dd>
              <dt>{t("construct.wheelbase_m")}</dt><dd data-testid="derived-wheelbase">{formatLength(derived["wheelbase_m"] ?? 0, units)}</dd>
              <dt>{t("construct.track_m")}</dt><dd>{formatLength(derived["track_m"] ?? 0, units)}</dd>
              <dt>{t("construct.turning_radius")}</dt><dd>{formatLength(derived["turning_radius_m"] ?? 0, units)}</dd>
              <dt>{t("construct.top_speed")}</dt><dd>{(derived["top_speed_m_s"] ?? 0).toFixed(2)} m/s</dd>
              <dt>{t("construct.cog")}</dt><dd>{formatLength((result?.derived["cog_m"] as number[] | undefined)?.[2] ?? 0, units)}</dd>
              <dt>{t("construct.runtime")}</dt><dd>{derived["battery_runtime_h"] != null ? `${(derived["battery_runtime_h"] * 60).toFixed(0)} min` : "–"}</dd>
            </dl>
          )}
          <div style={{ width: "100%" }}>
            {result && result.warnings.length === 0 && <span className="badge ok">{t("construct.no_warnings")}</span>}
            {result?.warnings.map((w) => <div key={w.code} className="warning">⚠ {w.message}</div>)}
          </div>
        </div>
      </section>
    </div>
  );
}
