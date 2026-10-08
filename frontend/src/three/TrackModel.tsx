import { memo } from "react";
import type { Primitive } from "../api/client";
import { toThreeQuat } from "./CarModel";

const CUT_HEIGHT = 0.3; // metres: "cutaway" view shows only the lower part of walls and tall objects

/** In cutaway mode tall boxes are drawn only up to CUT_HEIGHT so cars stay visible. */
export function cutaway(p: Primitive, enabled: boolean): Primitive {
  if (!enabled || p.kind !== "box") return p;
  const top = p.pos[2] + p.size[2];
  if (top <= CUT_HEIGHT) return p;
  const bottom = p.pos[2] - p.size[2];
  const half = (CUT_HEIGHT - bottom) / 2;
  return { ...p, pos: [p.pos[0], p.pos[1], bottom + half], size: [p.size[0], p.size[1], half] };
}

function Shape({ p }: { p: Primitive }) {
  const q = toThreeQuat(p.quat);
  if (p.kind === "plane") {
    return (
      <mesh position={p.pos} quaternion={q}>
        <planeGeometry args={[Math.min(p.size[0] * 2, 400), Math.min(p.size[1] * 2, 400)]} />
        <meshStandardMaterial color={p.color} />
      </mesh>
    );
  }
  if (p.kind === "cylinder") {
    return (
      <mesh position={p.pos} quaternion={q} rotation-x={Math.PI / 2}>
        <cylinderGeometry args={[p.size[0], p.size[0], p.size[1] * 2, 24]} />
        <meshStandardMaterial color={p.color} transparent={p.opacity < 1} opacity={p.opacity} />
      </mesh>
    );
  }
  return (
    <mesh position={p.pos} quaternion={q}>
      <boxGeometry args={[p.size[0] * 2, p.size[1] * 2, p.size[2] * 2]} />
      <meshStandardMaterial color={p.color} transparent={p.opacity < 1} opacity={p.opacity} />
    </mesh>
  );
}

export const TrackModel = memo(function TrackModel({ primitives, cut = true }: { primitives: Primitive[]; cut?: boolean }) {
  return (
    <group>
      {primitives.map((p, i) => (
        <Shape key={i} p={cutaway(p, cut)} />
      ))}
    </group>
  );
});
