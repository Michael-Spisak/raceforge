import { formatLength, useSettings } from "./settings";
import { mouseButtons } from "../three/Viewport";
import { MOUSE } from "three";

describe("settings", () => {
  it("formats lengths in mm and studs", () => {
    expect(formatLength(0.12, "mm")).toBe("120 mm");
    expect(formatLength(0.12, "studs")).toBe("15.0 studs");
  });

  it("persists the navigation preset", () => {
    useSettings.getState().setNavPreset("studio");
    expect(useSettings.getState().navPreset).toBe("studio");
    expect(localStorage.getItem("raceforge.navPreset")).toBe("studio");
  });

  it("maps presets to mouse buttons", () => {
    expect(mouseButtons("blender").MIDDLE).toBe(MOUSE.ROTATE);
    expect(mouseButtons("studio").RIGHT).toBe(MOUSE.ROTATE);
    expect(mouseButtons("fusion").MIDDLE).toBe(MOUSE.PAN);
  });
});
