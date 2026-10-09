import { type BufferGeometry, Group, Mesh, MeshStandardMaterial } from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { engineBase } from "../api/client";
import { LDRAW_COLOURS } from "./ldraw";

const cache = new Map<string, Promise<BufferGeometry | null>>();

/** Mesh of a 3D-printed part (spec 0019): binary STL from the engine, metres, core frame. */
export async function loadStl(url: string, colour: number): Promise<Group | null> {
  let pending = cache.get(url);
  if (!pending) {
    pending = new STLLoader().loadAsync(`${engineBase()}${url}`).then((g) => { g.computeVertexNormals(); return g; }).catch(() => null);
    cache.set(url, pending);
  }
  const geometry = await pending;
  if (!geometry) return null;
  const group = new Group();
  group.add(new Mesh(geometry, new MeshStandardMaterial({ color: LDRAW_COLOURS[colour] ?? "#d67923", roughness: 0.7 })));
  return group;
}
