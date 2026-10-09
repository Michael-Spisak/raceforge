import { useEffect, useMemo, useState } from "react";
import type { Group } from "three";
import type { CarScene, Schemas } from "../api/client";
import { useSettings } from "../store/settings";
import { LDRAW_COLOURS, loadPart } from "./ldraw";
import { loadStl } from "./stl";

type ScenePart = Schemas["ScenePart"];
export type Pose = [[number, number, number], [number, number, number, number]]; // pos, quat (w,x,y,z)

/** Three.js quaternions are (x, y, z, w). */
export function toThreeQuat(q: readonly number[]): [number, number, number, number] {
  return [q[1] ?? 0, q[2] ?? 0, q[3] ?? 0, q[0] ?? 1];
}

function PartMesh({ part, useLDraw }: { part: ScenePart; useLDraw: boolean }) {
  const [object, setObject] = useState<Group | null>(null);
  const colour = useSettings((st) => (st.colourMode === "real" ? part.real_color ?? part.color : part.color));
  useEffect(() => {
    let alive = true;
    if (part.mesh_url) {
      void loadStl(part.mesh_url, colour).then((g) => alive && setObject(g));
    } else if (useLDraw && part.ldraw_id) {
      void loadPart(part.ldraw_id, colour).then((g) => alive && setObject(g));
    }
    return () => {
      alive = false;
    };
  }, [part.ldraw_id, part.mesh_url, colour, useLDraw]);
  const lo = part.bbox_lo;
  const hi = part.bbox_hi;
  const centre: [number, number, number] = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
  const size: [number, number, number] = [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]];
  return (
    <group position={part.pos} quaternion={toThreeQuat(part.quat)}>
      {object ? (
        <primitive object={object} />
      ) : (
        <mesh position={centre}>
          <boxGeometry args={size} />
          <meshStandardMaterial color={LDRAW_COLOURS[colour] ?? "#8a8f99"} transparent opacity={part.ldraw_id ? 0.6 : 0.9} />
        </mesh>
      )}
    </group>
  );
}

interface Props {
  car: CarScene;
  /** Body poses in world coordinates; without poses all bodies sit at the origin (Construct view). */
  poses?: Record<string, Pose>;
  useLDraw?: boolean;
}

export function CarModel({ car, poses, useLDraw = true }: Props) {
  const bodies = useMemo(() => car.bodies, [car]);
  return (
    <group>
      {bodies.map((body) => {
        const pose = poses?.[body.name];
        return (
          <group
            key={body.name}
            position={pose ? pose[0] : [0, 0, 0]}
            quaternion={pose ? toThreeQuat(pose[1]) : [0, 0, 0, 1]}
          >
            {body.parts.map((p, i) => (
              <PartMesh key={`${p.key}-${i}`} part={p} useLDraw={useLDraw} />
            ))}
          </group>
        );
      })}
    </group>
  );
}
