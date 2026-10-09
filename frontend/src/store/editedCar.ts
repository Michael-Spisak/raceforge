import { create } from "zustand";
import type { QuickStartParams } from "../api/client";

interface EditedCarState {
  assembly: Record<string, unknown> | null;
  params: Partial<QuickStartParams>;
  set: (assembly: Record<string, unknown>, params: Partial<QuickStartParams>) => void;
}

/** The car open in the Construct editor (spec 0015), so Simulate can drive it. Session only. */
export const useEditedCar = create<EditedCarState>((set) => ({
  assembly: null,
  params: {},
  set: (assembly, params) => set({ assembly, params }),
}));
