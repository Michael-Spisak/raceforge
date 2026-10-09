import { Color, Group, LineSegments, Matrix4, Mesh, MeshStandardMaterial } from "three";
import { LDrawLoader } from "three/examples/jsm/loaders/LDrawLoader.js";
import { LDrawConditionalLineMaterial } from "three/examples/jsm/materials/LDrawConditionalLineMaterial.js";
import { ldrawBase } from "../api/client";

/** LDraw (−Y up, LDU) → core frame (Z up, metres): core = C · ldraw with C = [[1,0,0],[0,0,1],[0,−1,0]]. */
export const LDU_M = 0.0004;
export const LDRAW_TO_CORE = new Matrix4()
  .set(1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1)
  .multiply(new Matrix4().makeScale(LDU_M, LDU_M, LDU_M));

/** Function colours by LDraw colour code (see raceforge.construct.ldraw_export.COLOURS). */
export const LDRAW_COLOURS: Record<number, string> = {
  0: "#1b2a34", 1: "#1e5aa8", 2: "#00852b", 4: "#b40000", 14: "#fac80a", 15: "#f4f4f4", 16: "#8a8f99",
  19: "#e4cd9e", 25: "#d67923", 71: "#a0a5a9", 72: "#6c6e68",
};

let loader: LDrawLoader | null = null;
const cache = new Map<string, Promise<Group | null>>();
const materials = new Map<number, MeshStandardMaterial>();

function getLoader(): LDrawLoader {
  if (!loader) {
    loader = new LDrawLoader();
    loader.setConditionalLineMaterial(LDrawConditionalLineMaterial);
    loader.setPartsLibraryPath(ldrawBase());
    loader.smoothNormals = true;
  }
  return loader;
}

function materialFor(code: number): MeshStandardMaterial {
  let m = materials.get(code);
  if (!m) {
    m = new MeshStandardMaterial({ color: new Color(LDRAW_COLOURS[code] ?? LDRAW_COLOURS[16]), roughness: 0.55, metalness: 0.05 });
    materials.set(code, m);
  }
  return m;
}

/** Loads (once) and returns a fresh clone of the part, recoloured, in the core frame; null if unavailable. */
export async function loadPart(ldrawId: string, colour: number): Promise<Group | null> {
  let pending = cache.get(ldrawId);
  if (!pending) {
    pending = new Promise<Group | null>((resolve) => {
      getLoader().load(
        `${ldrawBase()}parts/${ldrawId}.dat`,
        (group) => resolve(group),
        undefined,
        () => resolve(null),
      );
    });
    cache.set(ldrawId, pending);
  }
  const base = await pending;
  if (!base) return null;
  const clone = base.clone(true);
  clone.traverse((o) => {
    if (o instanceof Mesh) o.material = materialFor(colour);
    if (o instanceof LineSegments) o.visible = false; // edges are noisy at this scale
  });
  const holder = new Group();
  holder.add(clone);
  clone.matrixAutoUpdate = false;
  clone.matrix.copy(LDRAW_TO_CORE);
  return holder;
}
