import { describe, expect, it } from "vitest";
import type { ScanDetail, ScanMesh } from "../api/client";
import { framing } from "../screens/ScansScreen";
import { meshArrays, trajectoryRuns } from "./ScanModel";

const b64 = (a: ArrayBufferView) =>
  btoa(String.fromCharCode(...new Uint8Array(a.buffer, a.byteOffset, a.byteLength)));

const mesh: ScanMesh = {
  sha256: "x",
  vertices: 4,
  faces: 2,
  total_faces: 2,
  positions_b64: b64(new Float32Array([0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 1])),
  indices_b64: b64(new Uint32Array([0, 1, 2, 0, 2, 3])),
  classes_b64: b64(new Uint8Array([2, 1])), // floor, wall
  classes: ["none", "wall", "floor", "ceiling", "table", "seat", "window", "door"],
};

function detail(kept: boolean[], segment: number[]): ScanDetail {
  return {
    sha256: "x",
    summary: {},
    segments: [],
    trajectory: kept.map((_, i) => [i, 0, 1] as [number, number, number]),
    trajectory_kept: kept,
    trajectory_segment: segment,
    bounds: [[-1, -2, 0], [3, 2, 2]],
  };
}

describe("scan mesh", () => {
  it("expands faces with one colour per face", () => {
    const { positions, colors } = meshArrays(mesh);
    expect(positions.length).toBe(18);
    expect(Array.from(positions.slice(9, 18))).toEqual([0, 0, 0, 1, 1, 0, 0, 1, 1]);
    // face 0 = floor (#e69f00), face 1 = wall (#56b4e9)
    expect(colors[0]).toBeCloseTo(0xe6 / 255);
    expect(colors[9]).toBeCloseTo(0x56 / 255);
    expect(colors[17]).toBeCloseTo(0xe9 / 255);
  });

  it("hides classes and cuts at a height", () => {
    expect(meshArrays(mesh, undefined, { hiddenClasses: [1] }).positions.length).toBe(9); // wall gone
    expect(meshArrays(mesh, undefined, { maxZ: 0.5 }).positions.length).toBe(9); // face 1 reaches z = 1
  });

  it("uses the pass colour when colouring by pass", () => {
    const { colors } = meshArrays(mesh, "#ff0000");
    expect(Array.from(colors.slice(0, 3))).toEqual([1, 0, 0]);
    expect(Array.from(colors.slice(9, 12))).toEqual([1, 0, 0]);
  });
});

describe("camera path", () => {
  it("splits kept and discarded runs and keeps them connected", () => {
    const runs = trajectoryRuns(detail([true, true, false, false, true], [0, 0, 0, 0, 0]));
    expect(runs.map((r) => r.kept)).toEqual([true, false, true]);
    expect(runs[1]?.points[0]).toEqual([1, 0, 1]); // starts where the kept run ended
  });

  it("does not connect segments (pause gap)", () => {
    const runs = trajectoryRuns(detail([true, true, true, true], [0, 0, 1, 1]));
    expect(runs).toHaveLength(2);
    expect(runs[1]?.points[0]).toEqual([2, 0, 1]);
  });

  it("frames the union of the selected passes", () => {
    const f = framing([detail([true, true], [0, 0])]);
    expect(f.target).toEqual([1, 0, 1]);
    expect(f.camera[2]).toBeGreaterThan(f.target[2]);
  });
});
