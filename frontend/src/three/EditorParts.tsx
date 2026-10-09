import { type ThreeEvent } from "@react-three/fiber";
import { useEffect, useState } from "react";
import type { Group } from "three";
import type { Schemas } from "../api/client";
import { toThreeQuat } from "./CarModel";
import { useSettings } from "../store/settings";
import { LDRAW_COLOURS, loadPart } from "./ldraw";
import { loadStl } from "./stl";

type EditorPartView = Schemas["EditorPartView"];

const pathKey = (p: readonly string[]) => p.join("/");

function EditorPart({ part, selected, flagged, onSelect }: { part: EditorPartView; selected: boolean; flagged: boolean; onSelect: (path: string[], additive: boolean) => void }) {
  const [object, setObject] = useState<Group | null>(null);
  const colour = useSettings((st) => (st.colourMode === "real" ? part.real_color ?? part.color : part.color));
  useEffect(() => {
    let alive = true;
    if (part.mesh_url) void loadStl(part.mesh_url, colour).then((g) => alive && setObject(g));
    else if (part.ldraw_id) void loadPart(part.ldraw_id, colour).then((g) => alive && setObject(g));
    return () => {
      alive = false;
    };
  }, [part.ldraw_id, part.mesh_url, colour]);
  const lo = part.bbox_lo;
  const hi = part.bbox_hi;
  const centre: [number, number, number] = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
  const size: [number, number, number] = [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]];
  const click = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    onSelect(part.path, e.nativeEvent.shiftKey);
  };
  return (
    <group position={part.pos} quaternion={toThreeQuat(part.quat)} onClick={click}>
      {object ? (
        <primitive object={object} />
      ) : (
        <mesh position={centre}>
          <boxGeometry args={size} />
          <meshStandardMaterial color={LDRAW_COLOURS[colour] ?? "#8a8f99"} transparent opacity={0.85} />
        </mesh>
      )}
      {(selected || flagged) && (
        <mesh position={centre}>
          <boxGeometry args={[size[0] + 0.002, size[1] + 0.002, size[2] + 0.002]} />
          <meshBasicMaterial color={selected ? "#ff9f1c" : "#e63946"} wireframe />
        </mesh>
      )}
    </group>
  );
}

/** Parts of the edited assembly; click selects (spec 0015). Connectors of the selection are dots. */
export function EditorParts({ parts, selected, flagged, onSelect }: {
  parts: EditorPartView[];
  selected: string[][];
  flagged?: ReadonlySet<string>; // path keys drawn in red (overlaps, rule violations)
  onSelect: (path: string[], additive: boolean) => void;
}) {
  const keys = new Set(selected.map(pathKey));
  const last = selected[selected.length - 1];
  const current = last ? parts.find((p) => pathKey(p.path) === pathKey(last)) : undefined;
  return (
    <group>
      {parts.map((p) => (
        <EditorPart key={pathKey(p.path)} part={p} selected={keys.has(pathKey(p.path))} flagged={!!flagged?.has(pathKey(p.path))} onSelect={onSelect} />
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
