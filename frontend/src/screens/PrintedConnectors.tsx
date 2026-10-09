import { Line } from "@react-three/drei";
import type { ThreeEvent } from "@react-three/fiber";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { BufferGeometry } from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { ApiError, api, engineBase, type PartSummary, type Schemas } from "../api/client";
import { Viewport } from "../three/Viewport";

type ConnectorDef = Schemas["ConnectorDef"];
type ConnectorType = ConnectorDef["type"];
const TYPES: ConnectorType[] = ["pin_hole", "axle_hole", "stud", "anti_stud", "screw_hole", "fixed_mount", "pin", "axle"];
const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));
const r4 = (v: number) => Math.round(v * 10000) / 10000; // 0.1 mm

/** Click on a 3D-printed part's mesh to put connectors on it (spec 0021). */
export function PrintedConnectors({ part, onClose }: { part: PartSummary; onClose: () => void }) {
  const { t } = useTranslation();
  const [geometry, setGeometry] = useState<BufferGeometry | null>(null);
  const [connectors, setConnectors] = useState<ConnectorDef[]>([]);
  const [type, setType] = useState<ConnectorType>("pin_hole");
  const [selected, setSelected] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!part.mesh_url) return;
    new STLLoader().loadAsync(`${engineBase()}${part.mesh_url}`).then((g) => { g.computeVertexNormals(); setGeometry(g); }).catch((e: unknown) => setError(message(e)));
    api.partConnectors(part.key).then(setConnectors).catch((e: unknown) => setError(message(e)));
  }, [part.key, part.mesh_url]);

  const add = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    const n = e.face?.normal;
    const ids = new Set(connectors.map((c) => c.id));
    let i = connectors.length + 1;
    while (ids.has(`c${i}`)) i += 1;
    const c: ConnectorDef = {
      id: `c${i}`, type,
      pos: [r4(e.point.x), r4(e.point.y), r4(e.point.z)],
      axis: n ? [r4(n.x), r4(n.y), r4(n.z)] : [0, 0, 1],
    };
    setConnectors([...connectors, c]);
    setSelected(c.id);
    setNote("");
  };
  const save = () => {
    setError("");
    api.setPartConnectors(part.key, connectors).then((cs) => { setConnectors(cs); setNote(t("parts.connectors_saved", { count: cs.length })); })
      .catch((e: unknown) => setError(message(e)));
  };
  const flip = (id: string) => setConnectors(connectors.map((c) => (c.id === id ? { ...c, axis: [-c.axis[0], -c.axis[1], -c.axis[2]] } : c)));

  return (
    <div style={{ display: "grid", gridTemplateRows: "1fr auto", height: "100%" }}>
      <Viewport camera={[0.08, -0.08, 0.07]} target={[0, 0, 0]} testId="printed-connectors-viewport">
        {geometry && (
          <mesh geometry={geometry} onClick={add}>
            <meshStandardMaterial color="#d67923" roughness={0.7} transparent opacity={0.85} />
          </mesh>
        )}
        {connectors.map((c) => {
          const end: [number, number, number] = [c.pos[0] + c.axis[0] * 0.008, c.pos[1] + c.axis[1] * 0.008, c.pos[2] + c.axis[2] * 0.008];
          const colour = c.id === selected ? "#ff9f1c" : c.type.includes("hole") || c.type === "anti_stud" ? "#2a9d8f" : "#e63946";
          return (
            <group key={c.id} onClick={(e) => { e.stopPropagation(); setSelected(c.id); }}>
              <mesh position={c.pos}><sphereGeometry args={[0.0012, 12, 12]} /><meshBasicMaterial color={colour} /></mesh>
              <Line points={[c.pos, end]} color={colour} lineWidth={2} />
            </group>
          );
        })}
      </Viewport>
      <div className="statusbar" style={{ flexWrap: "wrap", gap: 8 }} data-testid="printed-connectors">
        <strong style={{ width: "100%" }}>{t("parts.connectors_title", { name: part.name })}</strong>
        <label>{t("parts.new_connector")}{" "}
          <select value={type} onChange={(e) => setType(e.target.value as ConnectorType)}>
            {TYPES.map((x) => <option key={x} value={x}>{t(`connector.${x}`)}</option>)}
          </select>
        </label>
        <span className="muted">{t("parts.connectors_help")}</span>
        <ul className="list" style={{ width: "100%" }}>
          {connectors.map((c) => (
            <li key={c.id} aria-selected={c.id === selected} onClick={() => setSelected(c.id)}>
              {c.id} · {t(`connector.${c.type}`)} · {c.pos.map((v) => (v * 1000).toFixed(1)).join(", ")} mm
              <button type="button" style={{ marginLeft: 8 }} onClick={(e) => { e.stopPropagation(); flip(c.id); }}>{t("parts.flip_axis")}</button>
              <button type="button" style={{ marginLeft: 4 }} onClick={(e) => { e.stopPropagation(); setConnectors(connectors.filter((x) => x.id !== c.id)); }}>{t("tracks.delete")}</button>
            </li>
          ))}
        </ul>
        <button type="button" className="primary" onClick={save} data-testid="printed-connectors-save">{t("parts.save_connectors")}</button>
        <button type="button" onClick={onClose}>{t("parts.close")}</button>
        {note && <span>{note}</span>}
        {error && <span className="error" role="alert">{error}</span>}
      </div>
    </div>
  );
}
