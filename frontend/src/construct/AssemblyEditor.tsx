import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, type PartSummary, type QuickStartParams, type Schemas, versionLabel, workspace } from "../api/client";
import { formatLength, useSettings } from "../store/settings";
import { useEditedCar } from "../store/editedCar";
import { useWorkspace } from "../store/workspace";
import { EditorParts } from "../three/EditorParts";
import { RulesPanel } from "./RulesPanel";
import { Viewport } from "../three/Viewport";

type EditResponse = Schemas["AssemblyEditResponse"];
type EditOp = Partial<Schemas["EditOp"]>;
type Assembly = Record<string, unknown>;

const STUD = 0.008; // m
const LDU = 0.0004; // m
const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));
const same = (a: readonly string[] | null | undefined, b: readonly string[] | null | undefined) => !!a && !!b && a.join("/") === b.join("/");

function typing(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

/**
 * Construct editor v1 (spec 0015): select parts, move on the stud grid (Shift: 1 LDU), turn 90°,
 * delete, add from the catalogue, snap to compatible connectors, undo/redo, live derived data.
 */
export function AssemblyEditor({ start, params, onExit }: { start: Assembly; params: Partial<QuickStartParams>; onExit: () => void }) {
  const { t } = useTranslation();
  const units = useSettings((s) => s.units);
  const [view, setView] = useState<EditResponse | null>(null);
  const [selected, setSelected] = useState<string[] | null>(null);
  const [undo, setUndo] = useState<Assembly[]>([]);
  const [redo, setRedo] = useState<Assembly[]>([]);
  const [snapOn, setSnapOn] = useState(true);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [showOverlaps, setShowOverlaps] = useState(false);
  const [found, setFound] = useState<PartSummary[]>([]);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const busy = useRef(false);
  const publish = useEditedCar((s) => s.set);

  const run = useCallback((assembly: Assembly, op: EditOp, record: boolean) => {
    if (busy.current) return;
    busy.current = true;
    setError("");
    api.editAssembly({ assembly, quickstart: params as QuickStartParams, op: { kind: "none", ...op } as Schemas["EditOp"], snap: snapOn })
      .then((r) => {
        if (record && view) {
          setUndo((u) => [...u.slice(-99), view.assembly]);
          setRedo([]);
        }
        setView(r);
        if (!r.problems.length) publish(r.assembly, params);
        if (op.kind && op.kind !== "none") setSelected(r.selected ?? null);
        setNote(r.snapped ? t("editor.snapped", { a: r.snapped.connector, b: r.snapped.target.join(" / ") }) : "");
      })
      .catch((e: unknown) => setError(message(e)))
      .finally(() => { busy.current = false; });
  }, [params, snapOn, view, t, publish]);

  useEffect(() => {
    run(start, { kind: "none" }, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once for this start assembly
  }, [start]);

  const apply = (op: EditOp) => view && run(view.assembly, op, true);
  const doUndo = () => {
    const prev = undo[undo.length - 1];
    if (!prev || !view) return;
    setUndo((u) => u.slice(0, -1));
    setRedo((r) => [...r, view.assembly]);
    run(prev, { kind: "none" }, false);
  };
  const doRedo = () => {
    const next = redo[redo.length - 1];
    if (!next || !view) return;
    setRedo((r) => r.slice(0, -1));
    setUndo((u) => [...u, view.assembly]);
    run(next, { kind: "none" }, false);
  };

  // Keyboard: arrows/PageUp/PageDown move, R/T/Y turn about z/x/y, Delete, Ctrl+Z / Ctrl+Y.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (typing(e.target)) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); if (e.shiftKey) doRedo(); else doUndo(); return; }
      if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); doRedo(); return; }
      if (!selected) return;
      const step = e.shiftKey ? LDU : STUD;
      const moves: Record<string, [number, number, number]> = {
        ArrowLeft: [-step, 0, 0], ArrowRight: [step, 0, 0], ArrowUp: [0, step, 0], ArrowDown: [0, -step, 0],
        PageUp: [0, 0, step], PageDown: [0, 0, -step],
      };
      const turns: Record<string, "x" | "y" | "z"> = { r: "z", t: "x", y: "y" };
      if (moves[e.key]) { e.preventDefault(); apply({ kind: "move", path: selected, delta: moves[e.key] }); }
      else if (turns[e.key.toLowerCase()]) { e.preventDefault(); apply({ kind: "rotate", path: selected, axis: turns[e.key.toLowerCase()], turns: e.shiftKey ? -1 : 1 }); }
      else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); apply({ kind: "delete", path: selected }); }
      else if (e.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    if (!query.trim()) { setFound([]); return; }
    const id = window.setTimeout(() => void api.parts(query).then((ps) => setFound(ps.slice(0, 30))).catch(() => undefined), 200);
    return () => window.clearTimeout(id);
  }, [query]);

  const part = view?.parts.find((p) => same(p.path, selected));
  const flagged = new Set<string>([
    ...(view?.rules ?? []).flatMap((r) => r.paths.map((p) => p.join("/"))),
    ...(showOverlaps ? (view?.overlaps ?? []).flatMap((pair) => pair.map((p) => p.join("/"))) : []),
  ]);
  const derived = view?.derived as Record<string, number | null> | undefined;
  const addAt = (): [number, number, number] => {
    if (!part) return [0.06, 0, 0.12];
    return [part.pos[0], part.pos[1], part.pos[2] + 0.024]; // three plates above the selection
  };

  return (
    <div className="screen">
      <aside className="side">
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
          <button type="button" onClick={onExit}>{t("editor.back")}</button>
          <button type="button" onClick={doUndo} disabled={!undo.length} title="Ctrl+Z">{t("editor.undo")}</button>
          <button type="button" onClick={doRedo} disabled={!redo.length} title="Ctrl+Y">{t("editor.redo")}</button>
        </div>
        <div className="field"><label><input type="checkbox" checked={snapOn} onChange={(e) => setSnapOn(e.target.checked)} /> {t("editor.snap")}</label></div>
        {part ? (
          <div className="panel" data-testid="editor-selected">
            <strong>{part.name}</strong> <span className="muted">({part.key})</span>
            <div className="muted">{part.path.join(" / ")}</div>
            <div className="muted" data-testid="editor-pos">
              {part.pos.map((v) => (v * 1000).toFixed(1)).join(" · ")} mm
            </div>
            {part.linked && <p className="warning">{t("editor.linked")}</p>}
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 6 }}>
              <button type="button" onClick={() => apply({ kind: "rotate", path: part.path, axis: "z" })}>R ⟲z</button>
              <button type="button" onClick={() => apply({ kind: "rotate", path: part.path, axis: "x" })}>T ⟲x</button>
              <button type="button" onClick={() => apply({ kind: "rotate", path: part.path, axis: "y" })}>Y ⟲y</button>
              <button type="button" onClick={() => apply({ kind: "snap", path: part.path })}>{t("editor.snap_now")}</button>
              <button type="button" onClick={() => apply({ kind: "delete", path: part.path })} data-testid="editor-delete">{t("editor.delete")}</button>
            </div>
          </div>
        ) : <p className="muted">{t("editor.pick")}</p>}
        <div className="field">
          <label htmlFor="editor-filter">{t("editor.parts")}</label>
          <input id="editor-filter" value={filter} placeholder={t("editor.filter")} onChange={(e) => setFilter(e.target.value)} />
        </div>
        <ul className="list" style={{ maxHeight: 180, overflow: "auto" }} data-testid="editor-part-list">
          {view?.parts
            .filter((p) => !filter || `${p.name} ${p.key} ${p.path.join("/")}`.toLowerCase().includes(filter.toLowerCase()))
            .map((p) => (
              <li key={p.path.join("/")} aria-selected={same(p.path, selected)} onClick={() => setSelected(p.path)}>
                {p.path[p.path.length - 1]} <span className="muted">{p.name}</span>
              </li>
            ))}
        </ul>
        <div className="field">
          <label htmlFor="editor-add">{t("editor.add")}</label>
          <input id="editor-add" data-testid="editor-add" value={query} placeholder={t("editor.search")} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <ul className="list" style={{ maxHeight: 200, overflow: "auto" }}>
          {found.map((p) => (
            <li key={p.key} onClick={() => apply({ kind: "add", key: p.key, position: addAt() })} data-testid={`editor-add-${p.key}`}>
              {p.name} <span className="muted">{p.key}</span>
            </li>
          ))}
        </ul>
        <p className="muted">{t("editor.help")}</p>
        {view && (
          <RulesPanel rules={view.rules ?? []} budget={view.budget} showOverlaps={showOverlaps} onShowOverlaps={setShowOverlaps}
                      onSelect={setSelected} onSettingsSaved={() => run(view.assembly, { kind: "none" }, false)} />
        )}
        {view && <SaveAssembly assembly={view.assembly} disabled={view.problems.length > 0} />}
      </aside>
      <section className="main">
        <Viewport testId="editor-viewport">
          {view && <EditorParts parts={view.parts} selected={selected} flagged={flagged} onSelect={setSelected} />}
          <gridHelper args={[1, 125, "#9aa1ab", "#c4c9d0"]} rotation-x={Math.PI / 2} />
        </Viewport>
        <div className="statusbar" data-testid="editor-status">
          {derived && (
            <dl className="kv" style={{ gridTemplateColumns: "repeat(4, auto auto)", width: "100%" }}>
              <dt>{t("construct.mass")}</dt><dd data-testid="editor-mass">{((derived["mass_kg"] ?? 0) * 1000).toFixed(0)} g</dd>
              <dt>{t("construct.wheelbase_m")}</dt><dd>{formatLength(derived["wheelbase_m"] ?? 0, units)}</dd>
              <dt>{t("construct.track_m")}</dt><dd>{formatLength(derived["track_m"] ?? 0, units)}</dd>
              <dt>{t("construct.cog")}</dt><dd>{formatLength((view?.derived["cog_m"] as number[] | undefined)?.[2] ?? 0, units)}</dd>
              <dt>{t("editor.parts")}</dt><dd>{view?.parts.length ?? 0}</dd>
            </dl>
          )}
          <div style={{ width: "100%" }}>
            {note && <div>{note}</div>}
            {error && <div className="error" role="alert">{error}</div>}
            {view?.problems.map((p) => <div key={p} className="error">✖ {p}</div>)}
            {view?.warnings.map((w) => <div key={w.code} className="warning">⚠ {w.message}</div>)}
          </div>
        </div>
      </section>
    </div>
  );
}

function SaveAssembly({ assembly, disabled }: { assembly: Assembly; disabled: boolean }) {
  const { t } = useTranslation();
  const status = useWorkspace((s) => s.status);
  const refresh = useWorkspace((s) => s.refresh);
  const [slug, setSlug] = useState("car-a");
  const [msg, setMsg] = useState("");
  const [note, setNote] = useState("");
  if (!status?.logged_in || !status.workspace) return <p className="muted">{t("construct.save_login")}</p>;
  const save = () => {
    setNote("");
    workspace.saveAssembly(slug, assembly, msg)
      .then((v) => {
        const label = versionLabel(v);
        setNote(label ? t("team.saved", { version: label }) : t("team.saved_offline"));
        setMsg("");
        void refresh();
      })
      .catch((e: unknown) => setNote(message(e)));
  };
  return (
    <div className="panel">
      <div className="field">
        <label htmlFor="editor-slug">{t("team.slug")} ({status.workspace.name})</label>
        <input id="editor-slug" value={slug} onChange={(e) => setSlug(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="editor-msg">{t("team.message")}</label>
        <input id="editor-msg" value={msg} onChange={(e) => setMsg(e.target.value)} />
      </div>
      <button type="button" onClick={save} disabled={disabled}>{t("team.save_version")}</button>
      {note && <p>{note}</p>}
    </div>
  );
}
