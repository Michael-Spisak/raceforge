import type { components } from "./schema";

export type Schemas = components["schemas"];
export type Health = Schemas["Health"];
export type PartSummary = Schemas["PartSummary"];
export type QuickStartParams = Schemas["QuickStartParams"];
export type QuickstartResponse = Schemas["QuickstartResponse"];
export type QuickstartSchema = Schemas["QuickstartSchema"];
export type ControllerInfo = Schemas["ControllerInfo"];
export type CarScene = Schemas["CarScene"];
export type Primitive = Schemas["Primitive"];
export type SceneMessage = Schemas["SceneMessage"];
export type FrameMessage = Schemas["FrameMessage"];
export type ResultMessage = Schemas["ResultMessage"];
export type ErrorMessage = Schemas["ErrorMessage"];
export type SimStart = Schemas["SimStart"];
export type ReplaySummary = Schemas["ReplaySummary"];
export type ScanTrack = Schemas["ScanTrack"];
export type ScanPassRef = Schemas["ScanPassRef"];
export type ScanDetail = Schemas["ScanDetail"];
export type ScanMesh = Schemas["ScanMesh"];
export type WorkspaceStatus = Schemas["WorkspaceStatus"];
export type WorkspaceInfo = Schemas["WorkspaceInfo"];
export type LocalObject = Schemas["LocalObject"];
export type LocalVersion = Schemas["LocalVersion"];
export type SyncResult = Schemas["SyncResult"];
export type Conflict = Schemas["Conflict"];
export type InviteInfo = Schemas["InviteInfo"];
export type ApiTokenInfo = Schemas["ApiTokenInfo"];
export type UserInfo = Schemas["UserInfo"];
export type WorkspaceLogin = Schemas["WorkspaceLogin"];
export type WorkspaceRegister = Schemas["WorkspaceRegister"];
export type TokenRequest = Schemas["TokenRequest"];
export type TotpSetup = Schemas["TotpSetup"];
export type TrackScoutPairing = Schemas["TrackScoutPairing"];
export type InboxPass = Schemas["InboxPass"];
export type InboxAction = Schemas["InboxAction"]["action"];
export type ServerMessage = SceneMessage | FrameMessage | ResultMessage | ErrorMessage;

/** Engine base URL: same origin when served by the engine; the dev server proxies /api and /ldraw. */
export function engineBase(): string {
  const override = (import.meta.env.VITE_ENGINE_URL as string | undefined) ?? "";
  return override.replace(/\/$/, "");
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly fieldErrors: Record<string, string> = {},
  ) {
    super(message);
  }
}

interface ValidationDetail {
  loc: (string | number)[];
  msg: string;
}

/** Turns FastAPI 422 details into { field: message } (messages keep the engine's "nearest valid" hints). */
export function fieldErrorsFrom(detail: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!Array.isArray(detail)) return out;
  for (const d of detail as ValidationDetail[]) {
    const loc = d.loc.filter((p) => p !== "body");
    const msg = d.msg.replace(/^Value error, /, "");
    // Model-level validators report "<field>=<value> …": attach those to the field.
    const named = /^([a-z_][a-z0-9_]*)=/.exec(msg);
    const field = loc.length ? String(loc[0]) : (named?.[1] ?? "_");
    out[field] = msg;
  }
  return out;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${engineBase()}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => ({}));
    const detail = (body as { detail?: unknown }).detail;
    throw new ApiError(typeof detail === "string" ? detail : res.statusText, res.status, fieldErrorsFrom(detail));
  }
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export const api = {
  health: () => request<Health>("/api/v1/health"),
  parts: (query = "", category = "") =>
    request<PartSummary[]>(`/api/v1/parts?query=${encodeURIComponent(query)}&category=${encodeURIComponent(category)}`),
  quickstartSchema: () => request<QuickstartSchema>("/api/v1/quickstart/schema"),
  quickstart: (params: Partial<QuickStartParams>) =>
    request<QuickstartResponse>("/api/v1/quickstart", { method: "POST", body: JSON.stringify(params) }),
  exportText: (kind: "assembly" | "mpd" | "mjcf", params: Partial<QuickStartParams>) =>
    request<string>(`/api/v1/quickstart/export/${kind}`, { method: "POST", body: JSON.stringify(params) }),
  controllers: () => request<ControllerInfo[]>("/api/v1/controllers"),
  replay: (path: string) => request<ReplaySummary>("/api/v1/replays", { method: "POST", body: JSON.stringify({ path }) }),
  /** TrackScout passes the engine can show (spec 0009). */
  scans: () => request<ScanTrack[]>("/api/v1/scans"),
  openScan: (path: string) => request<ScanPassRef>("/api/v1/scans/open", { method: "POST", body: JSON.stringify({ path }) }),
  scan: (sha: string) => request<ScanDetail>(`/api/v1/scans/${sha}`),
  scanMesh: (sha: string, maxFaces = 300_000) => request<ScanMesh>(`/api/v1/scans/${sha}/mesh?max_faces=${maxFaces}`),
};

const W = "/api/v1/workspace";
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

/** Team workspace (spec 0006): the engine talks to the backend and keeps a local copy for offline use. */
export const workspace = {
  status: (probe = false) => request<WorkspaceStatus>(`${W}/status?probe=${probe}`),
  login: (body: WorkspaceLogin) => request<WorkspaceStatus>(`${W}/login`, post(body)),
  register: (body: WorkspaceRegister) => request<UserInfo>(`${W}/register`, post(body)),
  logout: () => request<WorkspaceStatus>(`${W}/logout`, { method: "POST" }),
  workspaces: () => request<WorkspaceInfo[]>(`${W}/workspaces`),
  createWorkspace: (name: string) => request<WorkspaceInfo>(`${W}/workspaces`, post({ name })),
  select: (workspace_id: string) => request<WorkspaceStatus>(`${W}/select`, post({ workspace_id })),
  sync: () => request<SyncResult>(`${W}/sync`, { method: "POST" }),
  objects: () => request<LocalObject[]>(`${W}/objects`),
  history: (objectId: string) => request<LocalVersion[]>(`${W}/objects/${objectId}/versions`),
  conflicts: () => request<Conflict[]>(`${W}/conflicts`),
  saveQuickstart: (slug: string, params: Partial<QuickStartParams>, message: string) =>
    request<LocalVersion>(`${W}/save/quickstart`, post({ slug, params, message })),
  saveFiles: (slug: string, paths: string[], message: string) =>
    request<LocalVersion>(`${W}/save/files`, post({ kind: "controller", slug, paths, message })),
  totpSetup: () => request<TotpSetup>(`${W}/totp/setup`, { method: "POST" }),
  totpVerify: (code: string) => request<UserInfo>(`${W}/totp/verify`, post({ code })),
  invites: () => request<InviteInfo[]>(`${W}/invites`),
  createInvite: (role: "member" | "admin") => request<InviteInfo>(`${W}/invites`, post({ role })),
  tokens: () => request<ApiTokenInfo[]>(`${W}/tokens`),
  createToken: (body: TokenRequest) => request<ApiTokenInfo>(`${W}/tokens`, post(body)),
  revokeToken: (id: string) => request<null>(`${W}/tokens/${id}`, { method: "DELETE" }),
  /** New `trackscout` token (read + edit) and the QR code the phone scans (spec 0007). */
  pairTrackScout: () => request<TrackScoutPairing>(`${W}/pair-trackscout`, { method: "POST" }),
  /** Passes received from a phone by cable/Bluetooth and their relay state (spec 0007). */
  inbox: () => request<InboxPass[]>(`${W}/trackscout/inbox`),
  receive: (source: "usb" | "bluetooth") => request<InboxPass[]>(`${W}/trackscout/receive`, post({ source })),
  chooseInbox: (id: string, action: InboxAction) => request<InboxPass>(`${W}/trackscout/inbox/${id}`, post({ action })),
};

/** "1.0.2", or null while a version only exists locally (numbered by the backend on sync). */
export function versionLabel(v: Pick<LocalVersion, "semver" | "pending">): string | null {
  return v.pending || !v.semver ? null : v.semver;
}

/** The backend answers a password-only login of a 2FA user with this detail. */
export function needsTotp(e: unknown): boolean {
  return e instanceof ApiError && e.status === 401 && e.message === "totp_required";
}

export function simSocketUrl(): string {
  const base = engineBase() || `${window.location.protocol}//${window.location.host}`;
  return `${base.replace(/^http/, "ws")}/api/v1/sim`;
}

export function ldrawBase(): string {
  return `${engineBase()}/ldraw/`;
}
