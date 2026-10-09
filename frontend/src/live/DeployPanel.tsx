import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, type BundleInfo, type ControllerInfo, type InstallResult } from "../api/client";

/** Remembered between sessions (never the telemetry token). */
function stored(key: string, fallback = ""): string {
  try {
    return localStorage.getItem(`rf.deploy.${key}`) ?? fallback;
  } catch {
    return fallback;
  }
}
function remember(key: string, value: string) {
  try {
    localStorage.setItem(`rf.deploy.${key}`, value);
  } catch {
    /* private window: nothing to remember */
  }
}

const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

/** Build a test-mode bundle and install it on the car over Wi-Fi (SSH) or a USB stick (spec 0012). */
export function DeployPanel() {
  const { t } = useTranslation();
  const [controllers, setControllers] = useState<ControllerInfo[]>([]);
  const [controller, setController] = useState(() => stored("controller"));
  const [params, setParams] = useState(() => stored("params"));
  const [carConfig, setCarConfig] = useState(() => stored("car", "controllers/car.example.yaml"));
  const [target, setTarget] = useState<"ssh" | "usb">(() => (stored("target") === "usb" ? "usb" : "ssh"));
  const [host, setHost] = useState(() => stored("host", "raceforge-car.local"));
  const [stick, setStick] = useState(() => stored("stick"));
  const [bundle, setBundle] = useState<BundleInfo | null>(null);
  const [busy, setBusy] = useState<"" | "build" | "install">("");
  const [error, setError] = useState("");
  const [result, setResult] = useState<InstallResult | null>(null);
  const [usbPath, setUsbPath] = useState("");
  const [usbPending, setUsbPending] = useState(false);

  useEffect(() => {
    void api.controllers().then((cs) => {
      setControllers(cs);
      setController((c) => c || cs[0]?.path || "");
    }).catch(() => undefined);
  }, []);

  const build = (e: FormEvent) => {
    e.preventDefault();
    remember("controller", controller);
    remember("params", params);
    remember("car", carConfig);
    setBusy("build");
    setError("");
    setBundle(null);
    setResult(null);
    setUsbPath("");
    api.buildBundle({ controller, params: params || null, car_config: carConfig, name: null })
      .then(setBundle)
      .catch((err: unknown) => setError(message(err)))
      .finally(() => setBusy(""));
  };

  const install = () => {
    if (!bundle) return;
    remember("target", target);
    remember(target === "ssh" ? "host" : "stick", target === "ssh" ? host : stick);
    setBusy("install");
    setError("");
    setResult(null);
    setUsbPath("");
    setUsbPending(false);
    api.deploy({ bundle: bundle.path, target, host: target === "ssh" ? host : null, stick: target === "usb" ? stick : null })
      .then((r) => {
        setResult(r.result ?? null);
        setUsbPath(r.usb_path ?? "");
      })
      .catch((err: unknown) => setError(message(err)))
      .finally(() => setBusy(""));
  };

  const checkUsb = () => {
    setError("");
    api.usbResult(stick)
      .then((r) => {
        setResult(r);
        setUsbPending(r === null);
      })
      .catch((err: unknown) => setError(message(err)));
  };

  const short = (d?: string | null) => (d ?? "").slice(0, 12);

  return (
    <form className="panel" onSubmit={build} data-testid="deploy-panel">
      <h3 style={{ marginTop: 0 }}>{t("deploy.title")}</h3>
      <div className="field">
        <label htmlFor="deploy-ctrl">{t("deploy.controller")}</label>
        <select id="deploy-ctrl" value={controllers.some((c) => c.path === controller) ? controller : ""}
                onChange={(e) => setController(e.target.value)}>
          <option value="" disabled>–</option>
          {controllers.map((c) => <option key={c.path} value={c.path}>{c.name}</option>)}
        </select>
      </div>
      <div className="field">
        <label htmlFor="deploy-ctrl-path">{t("deploy.controller_path")}</label>
        <input id="deploy-ctrl-path" data-testid="deploy-controller" value={controller} onChange={(e) => setController(e.target.value)} required />
      </div>
      <div className="field">
        <label htmlFor="deploy-params">{t("deploy.params")}</label>
        <input id="deploy-params" value={params} onChange={(e) => setParams(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="deploy-car">{t("deploy.car_config")}</label>
        <input id="deploy-car" data-testid="deploy-car" value={carConfig} onChange={(e) => setCarConfig(e.target.value)} required />
      </div>
      <button type="submit" disabled={busy !== "" || !controller || !carConfig} data-testid="deploy-build">
        {busy === "build" ? t("deploy.building") : t("deploy.build")}
      </button>
      <p className="muted">{t("deploy.race_hint")}</p>

      {bundle && (
        <>
          <p data-testid="deploy-bundle">
            {t("deploy.bundle", {
              name: bundle.name, digest: short(bundle.digest), car: bundle.car_name,
              limit: bundle.speed_limit_m_s == null ? t("deploy.car_max") : `${bundle.speed_limit_m_s} m/s`,
            })}
          </p>
          {bundle.warnings.map((w) => <p key={w} className="warning">{w}</p>)}
          <div className="field">
            <label>{t("deploy.target")}</label>
            <div style={{ display: "flex", gap: 12 }}>
              <label><input type="radio" checked={target === "ssh"} onChange={() => setTarget("ssh")} /> {t("deploy.ssh")}</label>
              <label><input type="radio" checked={target === "usb"} onChange={() => setTarget("usb")} /> {t("deploy.usb")}</label>
            </div>
          </div>
          {target === "ssh" ? (
            <div className="field">
              <label htmlFor="deploy-host">{t("deploy.host")}</label>
              <input id="deploy-host" data-testid="deploy-host" value={host} onChange={(e) => setHost(e.target.value)} />
            </div>
          ) : (
            <div className="field">
              <label htmlFor="deploy-stick">{t("deploy.stick")}</label>
              <input id="deploy-stick" data-testid="deploy-stick" value={stick} onChange={(e) => setStick(e.target.value)} />
            </div>
          )}
          <button type="button" className="primary" onClick={install} data-testid="deploy-install"
                  disabled={busy !== "" || (target === "ssh" ? !host : !stick)}>
            {busy === "install" ? t("deploy.installing") : t("deploy.install")}
          </button>
        </>
      )}

      {usbPath && (
        <>
          <p data-testid="deploy-usb-written">{t("deploy.usb_written", { path: usbPath })}</p>
          <button type="button" onClick={checkUsb} data-testid="deploy-check-usb">{t("deploy.check_usb")}</button>
          {usbPending && <p className="muted">{t("deploy.usb_pending")}</p>}
        </>
      )}
      {result && (
        <p data-testid="deploy-result" className={result.ok ? "" : "error"}>
          {result.ok
            ? t("deploy.installed", { name: result.name ?? "", digest: short(result.digest), detail: result.detail })
            : t("deploy.not_installed", { detail: result.detail }) + (result.rolled_back ? ` (${t("deploy.rolled_back")})` : "")}
          {result.service ? ` · ${t("deploy.service", { service: result.service })}` : ""}
        </p>
      )}
      {error && <p className="error" role="alert" data-testid="deploy-error">{error}</p>}
    </form>
  );
}
