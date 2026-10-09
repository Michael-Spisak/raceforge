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
export type CarPairingCode = Schemas["CarPairingCode"];
export type BundleRequest = Schemas["BundleRequest"];
export type BundleInfo = Schemas["BundleInfo"];
export type DeployRequest = Schemas["DeployRequest"];
export type DeployResponse = Schemas["DeployResponse"];
export type InstallResult = Schemas["InstallResult"];
export type TrainRace = Schemas["TrainRace"];
export type QuickTrack = Schemas["QuickTrack"];
export type QuickObstacle = Schemas["QuickObstacle"];
export type TrackEdit = Schemas["TrackEdit"];
export type EditObject = Schemas["EditObject"];
export type QuickTrackInfo = Schemas["QuickTrackInfo"];
export type QuickTrackPreview = Schemas["QuickTrackPreview"];
export type TrainJob = Schemas["TrainJob"];
export type TrainBenchRequest = Schemas["TrainBenchRequest"];
export type TrainTuneRequest = Schemas["TrainTuneRequest"];
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
  /** Whole LDraw library and the team's local catalogue additions (spec 0018). */
  ldrawParts: (query: string, limit = 80) =>
    request<Schemas["LDrawPart"][]>(`/api/v1/parts/ldraw?query=${encodeURIComponent(query)}&limit=${limit}`),
  addLocalPart: (req: Schemas["LocalPartRequest"]) =>
    request<PartSummary>("/api/v1/parts/local", { method: "POST", body: JSON.stringify(req) }),
  printedPreview: (req: Schemas["PrintedImportRequest"]) =>
    request<Schemas["PrintedPreview"]>("/api/v1/parts/printed/preview", { method: "POST", body: JSON.stringify(req) }),
  importPrinted: (req: Schemas["PrintedImportRequest"]) =>
    request<PartSummary>("/api/v1/parts/printed", { method: "POST", body: JSON.stringify(req) }),
  partConnectors: (key: string) => request<Schemas["ConnectorDef"][]>(`/api/v1/parts/${encodeURIComponent(key)}/connectors`),
  setPartConnectors: (key: string, defs: Schemas["ConnectorDef"][]) =>
    request<Schemas["ConnectorDef"][]>(`/api/v1/parts/${encodeURIComponent(key)}/connectors`, { method: "PUT", body: JSON.stringify(defs) }),
  controllers: () => request<ControllerInfo[]>("/api/v1/controllers"),
  replay: (path: string) => request<ReplaySummary>("/api/v1/replays", { method: "POST", body: JSON.stringify({ path }) }),
  /** TrackScout passes the engine can show (spec 0009). */
  scans: () => request<ScanTrack[]>("/api/v1/scans"),
  openScan: (path: string) => request<ScanPassRef>("/api/v1/scans/open", { method: "POST", body: JSON.stringify({ path }) }),
  scan: (sha: string) => request<ScanDetail>(`/api/v1/scans/${sha}`),
  scanMesh: (sha: string, maxFaces = 300_000) => request<ScanMesh>(`/api/v1/scans/${sha}/mesh?max_faces=${maxFaces}`),
  /** QR code for TrackScout's drive mode (spec 0010 C): the phone connects to the car directly. */
  exportAssembly: (kind: "assembly" | "mpd" | "mjcf" | "bom", assembly: Record<string, unknown>, params: Partial<QuickStartParams>) =>
    request<string>(`/api/v1/assembly/export/${kind}`, { method: "POST", body: JSON.stringify({ assembly, quickstart: params }) }),
  /** Construct editor (spec 0015): one operation on an assembly, evaluated by the engine. */
  editAssembly: (req: Schemas["AssemblyEditRequest"]) =>
    request<Schemas["AssemblyEditResponse"]>("/api/v1/assembly/edit", { method: "POST", body: JSON.stringify(req) }),
  /** Construct rule checker settings (spec 0016): prices, limits, budget. */
  constructSettings: () => request<Schemas["ConstructSettings"]>("/api/v1/construct/settings"),
  saveConstructSettings: (s: Schemas["ConstructSettings"]) =>
    request<Schemas["ConstructSettings"]>("/api/v1/construct/settings", { method: "PUT", body: JSON.stringify(s) }),
  /** Quick tracks (spec 0014): drawn corridors, saved in the engine. */
  quickTrackPreview: (q: QuickTrack) => request<QuickTrackPreview>("/api/v1/tracks/quick/preview", { method: "POST", body: JSON.stringify(q) }),
  quickTrackValidate: (q: QuickTrack) => request<Schemas["ValidationReport"]>("/api/v1/tracks/quick/validate", { method: "POST", body: JSON.stringify(q) }),
  quickTracks: () => request<QuickTrackInfo[]>("/api/v1/tracks/quick"),
  quickTrack: (name: string) => request<QuickTrack>(`/api/v1/tracks/quick/${encodeURIComponent(name)}`),
  saveQuickTrack: (name: string, q: QuickTrack) =>
    request<QuickTrackPreview>(`/api/v1/tracks/quick/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify(q) }),
  deleteQuickTrack: (name: string) => request<null>(`/api/v1/tracks/quick/${encodeURIComponent(name)}`, { method: "DELETE" }),
  /** Training jobs (spec 0013): one at a time in the engine; poll the job for progress. */
  trainBenchmark: (req: TrainBenchRequest) => request<TrainJob>("/api/v1/train/benchmark", { method: "POST", body: JSON.stringify(req) }),
  trainRL: (req: Schemas["TrainRLRequest"]) => request<TrainJob>("/api/v1/train/rl", { method: "POST", body: JSON.stringify(req) }),
  trainRecordings: () => request<Schemas["RecordingInfo"][]>("/api/v1/train/recordings"),
  trainBC: (req: Schemas["TrainBCRequest"]) => request<TrainJob>("/api/v1/train/bc", { method: "POST", body: JSON.stringify(req) }),
  trainTune: (req: TrainTuneRequest) => request<TrainJob>("/api/v1/train/tune", { method: "POST", body: JSON.stringify(req) }),
  trainJobs: () => request<TrainJob[]>("/api/v1/train/jobs"),
  trainCancel: (id: string) => request<TrainJob>(`/api/v1/train/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  /** Deploy (spec 0012): build a test-mode bundle, install it over SSH or write it to a USB stick. */
  buildBundle: (req: BundleRequest) => request<BundleInfo>("/api/v1/car/bundle", { method: "POST", body: JSON.stringify(req) }),
  deploy: (req: DeployRequest) => request<DeployResponse>("/api/v1/car/deploy", { method: "POST", body: JSON.stringify(req) }),
  usbResult: (stick: string) => request<InstallResult | null>(`/api/v1/car/deploy/usb-result?stick=${encodeURIComponent(stick)}`),
  carPairingCode: (url: string, token: string) =>
    request<CarPairingCode>("/api/v1/car/pairing-code", { method: "POST", body: JSON.stringify({ url, token: token || null }) }),
};

const W = "/api/v1/workspace";
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

/** Team workspace (spec 0006): the engine talks to the backend and keeps a local copy for offline use. */
export const workspace = {
  status: (probe = false) => request<WorkspaceStatus>(`${W}/status?probe=${probe}`),
  /** Team workers and jobs (spec 0020). */
  teamWorkers: () => request<Schemas["WorkerInfo"][]>(`${W}/workers`),
  teamJobs: () => request<Schemas["JobInfo"][]>(`${W}/jobs`),
  liveSessions: () => request<Schemas["LiveSession"][]>(`${W}/live`),
  localWorker: () => request<Schemas["LocalWorkerStatus"]>(`${W}/worker/local`),
  setLocalWorker: (body: Schemas["LocalWorkerUpdate"]) => request<Schemas["LocalWorkerStatus"]>(`${W}/worker/local`, { method: "PUT", body: JSON.stringify(body) }),
  submitTeamJob: (req: Schemas["TeamJobRequest"]) => request<Schemas["JobInfo"]>(`${W}/jobs`, post(req)),
  cancelTeamJob: (id: string) => request<Schemas["JobInfo"]>(`${W}/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  saveJobParams: (id: string, path: string) => request<{ path: string }>(`${W}/jobs/${encodeURIComponent(id)}/save-params`, post({ path })),
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
  saveAssembly: (slug: string, assembly: Record<string, unknown>, message: string) =>
    request<LocalVersion>(`${W}/save/assembly`, post({ slug, assembly, message })),
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

/** Engine relay to a real car's telemetry/teleop WebSocket (spec 0010). */
export function carSocketUrl(): string {
  const base = engineBase() || `${window.location.protocol}//${window.location.host}`;
  return `${base.replace(/^http/, "ws")}/api/v1/car/live`;
}

/** Read-only view of a teammate's car through the team relay (spec 0027). */
export function liveWatchUrl(session: string): string {
  const base = engineBase() || `${window.location.protocol}//${window.location.host}`;
  return `${base.replace(/^http/, "ws")}${W}/live/${encodeURIComponent(session)}/watch`;
}

export function ldrawBase(): string {
  return `${engineBase()}/ldraw/`;
}
