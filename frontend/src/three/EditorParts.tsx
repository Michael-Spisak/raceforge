import { type ThreeEvent } from "@react-three/fiber";
import { useEffect, useState } from "react";
import type { Group } from "three";
import type { Schemas } from "../api/client";
import { toThreeQuat } from "./CarModel";
import { LDRAW_COLOURS, loadPart } from "./ldraw";

type EditorPartView = Schemas["EditorPartView"];

const pathKey = (p: readonly string[]) => p.join("/");

function EditorPart({ part, selected, onSelect }: { part: EditorPartView; selected: boolean; onSelect: (path: string[]) => void }) {
  const [object, setObject] = useState<Group | null>(null);
  useEffect(() => {
    let alive = true;
    if (part.ldraw_id) void loadPart(part.ldraw_id, part.color).then((g) => alive && setObject(g));
    return () => {
      alive = false;
    };
  }, [part.ldraw_id, part.color]);
  const lo = part.bbox_lo;
  const hi = part.bbox_hi;
  const centre: [number, number, number] = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
  const size: [number, number, number] = [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]];
  const click = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    onSelect(part.path);
  };
  return (
    <group position={part.pos} quaternion={toThreeQuat(part.quat)} onClick={click}>
      {object ? (
        <primitive object={object} />
      ) : (
        <mesh position={centre}>
          <boxGeometry args={size} />
          <meshStandardMaterial color={LDRAW_COLOURS[part.color] ?? "#8a8f99"} transparent opacity={0.85} />
        </mesh>
      )}
      {selected && (
        <mesh position={centre}>
          <boxGeometry args={[size[0] + 0.002, size[1] + 0.002, size[2] + 0.002]} />
          <meshBasicMaterial color="#ff9f1c" wireframe />
        </mesh>
      )}
    </group>
  );
}

/** Parts of the edited assembly; click selects (spec 0015). Connectors of the selection are dots. */
export function EditorParts({ parts, selected, onSelect }: { parts: EditorPartView[]; selected: string[] | null; onSelect: (path: string[]) => void }) {
  const sel = selected ? pathKey(selected) : "";
  const current = parts.find((p) => pathKey(p.path) === sel);
  return (
    <group>
      {parts.map((p) => (
        <EditorPart key={pathKey(p.path)} part={p} selected={pathKey(p.path) === sel} onSelect={onSelect} />
      ))}
      {current?.connectors.map((c) => (
        <mesh key={c.id} position={c.pos}>
          <sphereGeometry args={[0.0015, 8, 8]} />
          <meshBasicMaterial color={c.type.includes("hole") || c.type === "anti_stud" ? "#2a9d8f" : "#e63946"} />
        </mesh>
      ))}
    </group>
  );
}
