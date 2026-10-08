import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ApiError,
  type ApiTokenInfo,
  type Conflict,
  type InviteInfo,
  type LocalObject,
  type InboxAction,
  type InboxPass,
  type LocalVersion,
  type TrackScoutPairing,
  type WorkspaceInfo,
  needsTotp,
  versionLabel,
  workspace,
} from "../api/client";
import { useWorkspace } from "../store/workspace";

const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

function LoginForm() {
  const { t } = useTranslation();
  const setStatus = useWorkspace((s) => s.set);
  const [server, setServer] = useState(() => localStorage.getItem("rf.server") ?? "");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [askTotp, setAskTotp] = useState(false);
  const [error, setError] = useState("");
  const [invite, setInvite] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [registered, setRegistered] = useState(false);

  const login = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      localStorage.setItem("rf.server", server);
      setStatus(await workspace.login({ server_url: server, username, password, totp: totp || null }));
    } catch (err) {
      if (needsTotp(err)) setAskTotp(true);
      else setError(message(err));
    }
  };
  const register = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await workspace.register({ server_url: server, invite, username, display_name: displayName || username, password });
      setRegistered(true);
    } catch (err) {
      setError(message(err));
    }
  };

  return (
    <div style={{ padding: 24, maxWidth: 420 }}>
      <h2>{t("team.login")}</h2>
      <form onSubmit={(e) => void login(e)}>
        <div className="field">
          <label htmlFor="server">{t("team.server")}</label>
          <input id="server" data-testid="login-server" value={server} placeholder="https://raceforge.example.org"
                 onChange={(e) => setServer(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="username">{t("team.username")}</label>
          <input id="username" data-testid="login-username" value={username} autoComplete="username"
                 onChange={(e) => setUsername(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="password">{t("team.password")}</label>
          <input id="password" data-testid="login-password" type="password" value={password} autoComplete="current-password"
                 onChange={(e) => setPassword(e.target.value)} required />
        </div>
        {askTotp && (
          <div className="field">
            <label htmlFor="totp">{t("team.totp")}</label>
            <input id="totp" data-testid="login-totp" inputMode="numeric" autoComplete="one-time-code" value={totp}
                   onChange={(e) => setTotp(e.target.value)} autoFocus required />
          </div>
        )}
        <button type="submit" data-testid="login-submit">{t("team.login")}</button>
        {error && <p className="error" role="alert">{error}</p>}
      </form>
      <details style={{ marginTop: 24 }}>
        <summary>{t("team.register")}</summary>
        <form onSubmit={(e) => void register(e)} style={{ marginTop: 8 }}>
          <div className="field">
            <label htmlFor="invite">{t("team.invite_link")}</label>
            <input id="invite" value={invite} onChange={(e) => setInvite(e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="display">{t("team.display_name")}</label>
            <input id="display" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </div>
          <p className="muted">{t("team.register_hint")}</p>
          <button type="submit">{t("team.register")}</button>
          {registered && <p className="badge ok">{t("team.registered")}</p>}
        </form>
      </details>
    </div>
  );
}

function TotpPanel({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const [setup, setSetup] = useState<{ secret: string; uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  return (
    <div className="panel" data-testid="totp-panel">
      <h3>{t("team.totp_title")}</h3>
      <p className="muted">{t("team.totp_why")}</p>
      {!setup ? (
        <button onClick={() => void workspace.totpSetup().then(setSetup).catch((e: unknown) => setError(message(e)))}>
          {t("team.totp_start")}
        </button>
      ) : (
        <form onSubmit={(e) => {
          e.preventDefault();
          void workspace.totpVerify(code).then(onDone).catch((err: unknown) => setError(message(err)));
        }}>
          <p>{t("team.totp_secret")}<br /><code data-testid="totp-secret" style={{ wordBreak: "break-all" }}>{setup.secret}</code></p>
          <div className="field">
            <label htmlFor="totp-code">{t("team.totp")}</label>
            <input id="totp-code" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} required />
          </div>
          <button type="submit">{t("team.totp_enable")}</button>
        </form>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

function AdminPanel() {
  const { t } = useTranslation();
  const [invites, setInvites] = useState<InviteInfo[]>([]);
  const [link, setLink] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(() => workspace.invites().then(setInvites).catch((e: unknown) => setError(message(e))), []);
  useEffect(() => void load(), [load]);
  return (
    <div className="panel">
      <h3>{t("team.invites")}</h3>
      <button data-testid="invite-create" onClick={() => void workspace.createInvite("member")
        .then((i) => { setLink(i.link ?? ""); void load(); }).catch((e: unknown) => setError(message(e)))}>
        {t("team.invite_create")}
      </button>
      {link && <p><code data-testid="invite-link" style={{ wordBreak: "break-all" }}>{link}</code><br />
        <span className="muted">{t("team.invite_hint")}</span></p>}
      {error && <p className="error" role="alert">{error}</p>}
      <ul className="list">
        {invites.map((i) => (
          <li key={i.id}>{i.role} · {new Date(i.created_at).toLocaleDateString()} · {i.used_at ? t("team.used") : t("team.open")}</li>
        ))}
      </ul>
    </div>
  );
}

function TokensPanel() {
  const { t } = useTranslation();
  const [tokens, setTokens] = useState<ApiTokenInfo[]>([]);
  const [name, setName] = useState("");
  const [client, setClient] = useState("desktop");
  const [scopes, setScopes] = useState<string[]>(["read"]);
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(() => workspace.tokens().then(setTokens).catch((e: unknown) => setError(message(e))), []);
  useEffect(() => void load(), [load]);
  const toggle = (s: string) => setScopes((x) => (x.includes(s) ? x.filter((y) => y !== s) : [...x, s]));
  const create = (e: FormEvent) => {
    e.preventDefault();
    workspace.createToken({ name, client, scopes: scopes as ("read" | "sim_train" | "edit" | "admin")[] })
      .then((tok) => { setSecret(tok.token ?? ""); setName(""); void load(); })
      .catch((err: unknown) => setError(message(err)));
  };
  return (
    <div className="panel">
      <h3>{t("team.tokens")}</h3>
      <form onSubmit={create}>
        <div className="field">
          <label htmlFor="tok-name">{t("team.token_name")}</label>
          <input id="tok-name" value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="tok-client">{t("team.token_client")}</label>
          <select id="tok-client" value={client} onChange={(e) => setClient(e.target.value)}>
            {["desktop", "cli", "worker", "mcp", "trackscout"].map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div className="field" role="group" aria-label={t("team.scopes")}>
          {["read", "sim_train", "edit", "admin"].map((s) => (
            <label key={s}><input type="checkbox" checked={scopes.includes(s)} onChange={() => toggle(s)} /> {s}</label>
          ))}
        </div>
        <button type="submit">{t("team.token_create")}</button>
      </form>
      {secret && <p><code style={{ wordBreak: "break-all" }}>{secret}</code><br /><span className="muted">{t("team.token_once")}</span></p>}
      {error && <p className="error" role="alert">{error}</p>}
      <ul className="list">
        {tokens.map((tok) => (
          <li key={tok.id}>
            {tok.name} ({tok.client}) · {tok.scopes.join(", ")} · {tok.user}
            {tok.revoked ? <span className="badge bad"> {t("team.revoked")}</span>
              : <button style={{ marginLeft: 8 }} onClick={() => void workspace.revokeToken(tok.id).then(load)}>{t("team.revoke")}</button>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function TrackScoutPanel({ onPaired }: { onPaired: () => void }) {
  const { t } = useTranslation();
  const [pairing, setPairing] = useState<TrackScoutPairing | null>(null);
  const [error, setError] = useState("");
  const pair = () => {
    setError("");
    workspace.pairTrackScout().then((p) => { setPairing(p); onPaired(); }).catch((e: unknown) => setError(message(e)));
  };
  return (
    <div className="panel" data-testid="trackscout-panel">
      <h3>{t("team.trackscout")}</h3>
      <button data-testid="trackscout-pair" onClick={pair}>{t("team.trackscout_pair")}</button>
      {pairing && (
        <div style={{ marginTop: 8 }}>
          <img data-testid="trackscout-qr" alt={t("team.trackscout_qr")} width={240} height={240}
               style={{ background: "#fff", padding: 8 }}
               src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(pairing.qr_svg)}`} />
          {pairing.warning === "loopback" && (
            <p className="warning" role="alert" data-testid="trackscout-warning">{t("team.trackscout_loopback")}</p>
          )}
          <p className="muted">{t("team.trackscout_hint")}</p>
          <code data-testid="trackscout-link" style={{ wordBreak: "break-all", fontSize: 11 }}>{pairing.url}</code>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <TrackScoutInbox />
    </div>
  );
}

const MB = 1_000_000;

/** Passes a phone sent to this laptop (cable/Bluetooth) and the relay choice per pass (spec 0007). */
function TrackScoutInbox() {
  const { t } = useTranslation();
  const [items, setItems] = useState<InboxPass[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const load = useCallback(() => workspace.inbox().then(setItems).catch(() => undefined), []);
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 10_000); // the relay keeps working in the background
    return () => window.clearInterval(timer);
  }, [load]);
  const receive = (source: "usb" | "bluetooth") => {
    setBusy(true);
    setNote(t("team.trackscout_receiving"));
    workspace.receive(source)
      .then((got) => setNote(t("team.trackscout_received", { count: got.length })))
      .catch((e: unknown) => setNote(message(e)))
      .finally(() => { setBusy(false); void load(); });
  };
  const choose = (id: string, action: InboxAction) =>
    void workspace.chooseInbox(id, action).then(load).catch((e: unknown) => setNote(message(e)));
  const detail = (p: InboxPass) => {
    if (p.state !== "waiting") return null;
    if (p.note === "offline") return t("team.trackscout_offline");
    if (p.note === "slow" && p.rate_bps != null)
      return t("team.trackscout_slow", { rate: (p.rate_bps / MB).toFixed(2), minutes: Math.round((p.eta_s ?? 0) / 60) });
    return p.note || null;
  };
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <button data-testid="trackscout-receive-usb" disabled={busy} onClick={() => receive("usb")}>{t("team.trackscout_receive_usb")}</button>
        <button data-testid="trackscout-receive-ble" disabled={busy} onClick={() => receive("bluetooth")}>{t("team.trackscout_receive_ble")}</button>
      </div>
      {note && <p data-testid="trackscout-note" className="muted">{note}</p>}
      {items.length > 0 && <h4>{t("team.trackscout_inbox")}</h4>}
      <ul className="list" data-testid="trackscout-inbox">
        {items.map((p) => (
          <li key={p.id} data-testid={`inbox-${p.id}`}>
            <strong>{p.project}</strong> <span className="muted">{p.pass_type} · {(p.size / MB).toFixed(1)} MB</span>{" "}
            <span className={`badge${p.state === "uploaded" ? " ok" : ""}`}>{t(`team.trackscout_state_${p.state}`)}</span>
            {p.version && <span className="muted"> · {p.version}</span>}
            {detail(p) && <div className="warning">{detail(p)}</div>}
            {(p.state === "waiting" || p.state === "local_only") && (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
                <button onClick={() => choose(p.id, "upload_now")}>{t("team.trackscout_upload_now")}</button>
                <button onClick={() => choose(p.id, "when_faster")}>{t("team.trackscout_when_faster")}</button>
                {p.state !== "local_only" && <button onClick={() => choose(p.id, "keep_local")}>{t("team.trackscout_keep_local")}</button>}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function History({ object }: { object: LocalObject }) {
  const { t } = useTranslation();
  const [versions, setVersions] = useState<LocalVersion[]>([]);
  useEffect(() => void workspace.history(object.id).then((v) => setVersions([...v].reverse())), [object]);
  return (
    <div className="panel" data-testid="history">
      <h3>{t("team.history")}: {object.slug}</h3>
      <ul className="list">
        {versions.map((v) => (
          <li key={v.id} data-testid="history-item">
            <strong>{versionLabel(v) ?? t("team.pending")}</strong>
            {v.branch && <span className="badge bad" style={{ marginLeft: 6 }}>{t("team.branch")}</span>}
            {" "}{v.message || <span className="muted">–</span>}
            <span className="muted"> · {v.author} · {new Date(v.created_at).toLocaleString()}</span>
            {v.error && <span className="error"> · {v.error}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function TeamScreen() {
  const { t } = useTranslation();
  const { status, refresh, set: setStatus } = useWorkspace();
  const [spaces, setSpaces] = useState<WorkspaceInfo[]>([]);
  const [objects, setObjects] = useState<LocalObject[]>([]);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [selected, setSelected] = useState<LocalObject | null>(null);
  const [newSpace, setNewSpace] = useState("");
  const [note, setNote] = useState("");
  const [ctrl, setCtrl] = useState({ slug: "", path: "", message: "" });
  const [tokensRev, setTokensRev] = useState(0);

  const reload = useCallback(async () => {
    await refresh();
    setSpaces(await workspace.workspaces().catch(() => []));
    setObjects(await workspace.objects().catch(() => []));
    setConflicts(await workspace.conflicts().catch(() => []));
  }, [refresh]);

  useEffect(() => {
    void refresh(true);
  }, [refresh]);
  useEffect(() => {
    if (status?.logged_in) void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload when login or workspace changes
  }, [status?.logged_in, status?.workspace?.id]);

  if (!status) return <p style={{ padding: 24 }}>{t("common.loading")}</p>;
  if (!status.logged_in) return <LoginForm />;

  const sync = () => {
    setNote("");
    workspace.sync()
      .then((r) => { setNote(t("team.synced", { pushed: r.pushed, pulled: r.pulled })); void reload(); })
      .catch((e: unknown) => setNote(message(e)));
  };
  const saveController = (e: FormEvent) => {
    e.preventDefault();
    workspace.saveFiles(ctrl.slug, [ctrl.path], ctrl.message)
      .then((v) => { setNote(t("team.saved", { version: versionLabel(v) ?? t("team.pending") })); void reload(); })
      .catch((err: unknown) => setNote(message(err)));
  };

  return (
    <div className="screen">
      <aside className="side">
        <div className="panel">
          <div data-testid="team-user"><strong>{status.user?.display_name}</strong> <span className="muted">({status.user?.role})</span></div>
          <div className="muted" style={{ wordBreak: "break-all" }}>{status.server_url}</div>
          <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
            <button data-testid="sync" onClick={sync}>{t("team.sync")}</button>
            <button onClick={() => void workspace.logout().then(setStatus)}>{t("team.logout")}</button>
          </div>
          {status.pending > 0 && <p className="warning">{t("team.pending_count", { count: status.pending })}</p>}
          {note && <p data-testid="team-note">{note}</p>}
        </div>
        <div className="field">
          <label htmlFor="ws">{t("team.workspace")}</label>
          <select id="ws" data-testid="ws-select" value={status.workspace?.id ?? ""}
                  onChange={(e) => void workspace.select(e.target.value).then(setStatus)}>
            <option value="" disabled>{t("team.pick_workspace")}</option>
            {spaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </div>
        <form className="field" onSubmit={(e) => {
          e.preventDefault();
          void workspace.createWorkspace(newSpace).then((w) => workspace.select(w.id)).then((s) => { setStatus(s); setNewSpace(""); })
            .catch((err: unknown) => setNote(message(err)));
        }}>
          <label htmlFor="new-ws">{t("team.new_workspace")}</label>
          <div style={{ display: "flex", gap: 6 }}>
            <input id="new-ws" data-testid="ws-new" value={newSpace} onChange={(e) => setNewSpace(e.target.value)} required />
            <button type="submit" data-testid="ws-create">{t("team.create")}</button>
          </div>
        </form>
        {status.workspace && (
          <form onSubmit={saveController} className="panel">
            <h3>{t("team.save_controller")}</h3>
            <div className="field">
              <label htmlFor="ctrl-slug">{t("team.slug")}</label>
              <input id="ctrl-slug" value={ctrl.slug} pattern="[a-z0-9][a-z0-9-]{1,62}" required
                     onChange={(e) => setCtrl({ ...ctrl, slug: e.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="ctrl-path">{t("team.file_path")}</label>
              <input id="ctrl-path" value={ctrl.path} required onChange={(e) => setCtrl({ ...ctrl, path: e.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="ctrl-msg">{t("team.message")}</label>
              <input id="ctrl-msg" value={ctrl.message} onChange={(e) => setCtrl({ ...ctrl, message: e.target.value })} />
            </div>
            <button type="submit">{t("team.save_version")}</button>
          </form>
        )}
      </aside>
      <section style={{ overflow: "auto", padding: 12 }}>
        {conflicts.length > 0 && (
          <div className="panel warning" role="alert" data-testid="conflicts">
            {t("team.conflicts", { count: conflicts.length })}
            <ul>{conflicts.map((c) => <li key={`${c.object_id}-${c.parent ?? ""}`}>{c.slug}: {c.versions.map((v) => versionLabel(v) ?? t("team.pending")).join(" / ")}</li>)}</ul>
          </div>
        )}
        <div className="panel">
          <h3>{t("team.objects")}</h3>
          {objects.length === 0 && <p className="muted">{t("team.no_objects")}</p>}
          <ul className="list" data-testid="objects">
            {objects.map((o) => (
              <li key={o.id} aria-selected={selected?.id === o.id} onClick={() => setSelected(o)} data-testid={`object-${o.slug}`}>
                <span className="muted">{o.kind}</span> <strong>{o.slug}</strong>{" "}
                {o.latest && (versionLabel(o.latest) ?? <span className="badge">{t("team.pending")}</span>)}
              </li>
            ))}
          </ul>
        </div>
        {selected && <History object={selected} />}
        {status.user && !status.user.totp_enabled && <TotpPanel onDone={() => void refresh()} />}
        {status.user?.role === "admin" && status.user.totp_enabled && <AdminPanel />}
        {status.workspace && <TrackScoutPanel onPaired={() => setTokensRev((n) => n + 1)} />}
        <TokensPanel key={tokensRev} />
      </section>
    </div>
  );
}
