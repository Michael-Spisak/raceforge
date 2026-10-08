import { create } from "zustand";
import { type WorkspaceStatus, workspace } from "../api/client";

interface WorkspaceState {
  status: WorkspaceStatus | null;
  refresh: (probe?: boolean) => Promise<void>;
  set: (status: WorkspaceStatus) => void;
}

/** Login / online state of the team workspace, shared by the status badge and the screens. */
export const useWorkspace = create<WorkspaceState>((set) => ({
  status: null,
  refresh: async (probe = false) => {
    try {
      set({ status: await workspace.status(probe) });
    } catch {
      set({ status: null });
    }
  },
  set: (status) => set({ status }),
}));

export type Badge = "none" | "online" | "offline";

export function badgeOf(s: WorkspaceStatus | null): Badge {
  if (!s?.logged_in) return "none";
  return s.online ? "online" : "offline";
}
