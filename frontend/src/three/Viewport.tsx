import { OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import type { ReactNode, RefObject } from "react";
import { MOUSE, Object3D, Vector3 } from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { type NavPreset, useSettings } from "../store/settings";

// Scene convention = RaceForge core frame: right-handed, Z up, metres.
Object3D.DEFAULT_UP.set(0, 0, 1);

/** Mouse mappings approximating the three navigation presets (plan §1). */
export function mouseButtons(preset: NavPreset): { LEFT?: MOUSE; MIDDLE?: MOUSE; RIGHT?: MOUSE } {
  switch (preset) {
    case "blender": // MMB orbit, RMB pan (Shift+MMB in Blender), wheel zoom
      return { LEFT: undefined, MIDDLE: MOUSE.ROTATE, RIGHT: MOUSE.PAN };
    case "studio": // BrickLink Studio: RMB orbit, MMB pan
      return { LEFT: undefined, MIDDLE: MOUSE.PAN, RIGHT: MOUSE.ROTATE };
    case "fusion": // Fusion 360: MMB pan, LMB orbit (Shift+MMB in Fusion)
      return { LEFT: MOUSE.ROTATE, MIDDLE: MOUSE.PAN, RIGHT: undefined };
  }
}

interface Props {
  children: ReactNode;
  camera?: [number, number, number];
  target?: [number, number, number];
  controlsRef?: RefObject<OrbitControlsImpl | null>;
  testId?: string;
}

export function Viewport({ children, camera = [0.35, -0.35, 0.3], target = [0.06, 0, 0.05], controlsRef, testId }: Props) {
  const preset = useSettings((s) => s.navPreset);
  return (
    <div className="viewport" data-testid={testId ?? "viewport"} onContextMenu={(e) => e.preventDefault()}>
      <Canvas camera={{ position: camera, fov: 45, near: 0.005, far: 500, up: [0, 0, 1] }} dpr={[1, 2]} shadows={false}>
        <color attach="background" args={["#d9dde3"]} />
        <hemisphereLight args={["#ffffff", "#8a8f99", 1.2]} position={[0, 0, 1]} />
        <directionalLight position={new Vector3(2, -3, 5)} intensity={1.6} />
        <OrbitControls
          ref={controlsRef}
          makeDefault
          target={target}
          mouseButtons={mouseButtons(preset)}
          enableDamping
        />
        {children}
      </Canvas>
    </div>
  );
}
