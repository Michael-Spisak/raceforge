import { Line } from "@react-three/drei";
import { memo, useMemo } from "react";
import { BufferAttribute, BufferGeometry, DoubleSide } from "three";
import type { ScanDetail, ScanMesh } from "../api/client";

/** Colour-blind-safe colours for ARKit's mesh classes (spec 0009), index = class id. */
export const CLASS_COLORS = ["#b8b8b8", "#56b4e9", "#e69f00", "#dddddd", "#009e73", "#f0e442", "#0072b2", "#d55e00"];
/** One colour per overlaid pass ("colour by pass"). */
export const PASS_COLORS = ["#0072b2", "#e69f00", "#009e73", "#cc79a7", "#d55e00", "#56b4e9", "#f0e442"];

function bytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function rgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

export interface MeshFilter {
  /** Class ids to leave out (e.g. the ceiling, so the corridor is visible from above). */
  hiddenClasses?: number[];
  /** Leave out faces whose highest point is above this height (metres, RaceForge Z). */
  maxZ?: number;
}

/**
 * Non-indexed triangle soup with one colour per face (a vertex can belong to faces of different classes).
 * `passColor` set → every face gets that colour instead of its class colour.
 */
export function meshArrays(
  mesh: ScanMesh, passColor?: string, filter: MeshFilter = {},
): { positions: Float32Array; colors: Float32Array } {
  const pos = new Float32Array(bytes(mesh.positions_b64).buffer);
  const idx = new Uint32Array(bytes(mesh.indices_b64).buffer);
  const cls = bytes(mesh.classes_b64);
  const palette = CLASS_COLORS.map(rgb);
  const fixed = passColor ? rgb(passColor) : null;
  const hidden = new Set(filter.hiddenClasses ?? []);
  const maxZ = filter.maxZ ?? Infinity;
  const keep: number[] = [];
  for (let f = 0; f < mesh.faces; f++) {
    if (hidden.has(cls[f] ?? 0)) continue;
    if (maxZ < Infinity) {
      let top = -Infinity;
      for (let k = 0; k < 3; k++) top = Math.max(top, pos[(idx[f * 3 + k] ?? 0) * 3 + 2] ?? 0);
      if (top > maxZ) continue;
    }
    keep.push(f);
  }
  const positions = new Float32Array(keep.length * 9);
  const colors = new Float32Array(keep.length * 9);
  for (let n = 0; n < keep.length; n++) {
    const f = keep[n] ?? 0;
    const c = fixed ?? palette[cls[f] ?? 0] ?? [0.7, 0.7, 0.7];
    for (let k = 0; k < 3; k++) {
      const v = idx[f * 3 + k] ?? 0;
      const o = n * 9 + k * 3;
      positions[o] = pos[v * 3] ?? 0;
      positions[o + 1] = pos[v * 3 + 1] ?? 0;
      positions[o + 2] = pos[v * 3 + 2] ?? 0;
      colors[o] = c[0];
      colors[o + 1] = c[1];
      colors[o + 2] = c[2];
    }
  }
  return { positions, colors };
}

/** Splits the camera path into runs of kept / discarded points (drawn in different colours). */
export function trajectoryRuns(detail: ScanDetail): { kept: boolean; points: [number, number, number][] }[] {
  const runs: { kept: boolean; points: [number, number, number][] }[] = [];
  detail.trajectory.forEach((p, i) => {
    const kept = detail.trajectory_kept[i] ?? true;
    const segment = detail.trajectory_segment[i] ?? 0;
    const last = runs[runs.length - 1];
    const prevSegment = i > 0 ? detail.trajectory_segment[i - 1] : segment;
    if (last && last.kept === kept && prevSegment === segment) last.points.push(p);
    else {
      // start the new run at the previous point so the line stays continuous within a segment
      const tail = last?.points[last.points.length - 1];
      const start = tail && prevSegment === segment ? [tail] : [];
      runs.push({ kept, points: [...start, p] });
    }
  });
  return runs.filter((r) => r.points.length >= 2);
}

export const ScanMeshView = memo(function ScanMeshView(
  { mesh, passColor, hideCeiling = false, maxZ }: { mesh: ScanMesh; passColor?: string; hideCeiling?: boolean; maxZ?: number },
) {
  const geometry = useMemo(() => {
    const ceiling = mesh.classes.indexOf("ceiling");
    const filter: MeshFilter = { hiddenClasses: hideCeiling && ceiling >= 0 ? [ceiling] : [], maxZ };
    const { positions, colors } = meshArrays(mesh, passColor, filter);
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(positions, 3));
    g.setAttribute("color", new BufferAttribute(colors, 3));
    g.computeVertexNormals();
    return g;
  }, [mesh, passColor, hideCeiling, maxZ]);
  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial vertexColors side={DoubleSide} roughness={0.9} metalness={0} />
    </mesh>
  );
});

export function ScanTrajectory({ detail, color = "#0072b2" }: { detail: ScanDetail; color?: string }) {
  const runs = useMemo(() => trajectoryRuns(detail), [detail]);
  const start = detail.trajectory[0];
  return (
    <group>
      {runs.map((r, i) => (
        <Line key={i} points={r.points} color={r.kept ? color : "#d62728"} lineWidth={r.kept ? 3 : 2}
              dashed={!r.kept} dashSize={0.05} gapSize={0.04} />
      ))}
      {start && (
        <mesh position={start}>
          <sphereGeometry args={[0.06, 16, 12]} />
          <meshStandardMaterial color={color} />
        </mesh>
      )}
    </group>
  );
}
