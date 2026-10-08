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
};

export function simSocketUrl(): string {
  const base = engineBase() || `${window.location.protocol}//${window.location.host}`;
  return `${base.replace(/^http/, "ws")}/api/v1/sim`;
}

export function ldrawBase(): string {
  return `${engineBase()}/ldraw/`;
}
