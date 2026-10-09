"""This computer as a team worker, switched on in the app (spec 0020 part C).

Enabling registers the computer in the current workspace (once; the ``worker`` token goes to
``worker.json``, owner-only) and runs the worker loop in a thread of the engine. The races run in
their own processes (spec 0013), so the engine stays responsive. Disabling stops the loop; a
running job is paused after the current race/trial and goes back to the team queue.
"""

import contextlib
import dataclasses
import socket
import threading
from collections import deque
from datetime import datetime

from raceforge.api.models import LocalWorkerStatus, LocalWorkerUpdate, WorkerPolicy
from raceforge.api.worker_policy import Probe, SystemProbe, decide
from raceforge.api.worker_runner import WorkerConfig, load_config, run_worker, save_config
from raceforge.workspace.client import BackendClient, BackendError, ClientFactory, OfflineError
from raceforge.workspace.sync import Workspace


class LocalWorker:
    def __init__(self, ws: Workspace, factory: ClientFactory, probe: Probe | None = None) -> None:
        self.ws = ws
        self.factory = factory
        self.probe = probe or SystemProbe()
        self.path = ws.root.parent / "worker.json"
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._log: deque[str] = deque(maxlen=50)
        cfg = load_config(self.path)
        self._policy = cfg.worker_policy() if cfg else WorkerPolicy()

    def _config(self) -> WorkerConfig | None:
        cfg = load_config(self.path)
        if cfg is None or cfg.workspace_id != self.ws.workspace_id:
            return None  # registered for another workspace (or never): register again
        return cfg

    def status(self) -> LocalWorkerStatus:
        cfg = self._config()
        avail = decide(self._policy, datetime.now(), self.probe)
        running = self._thread is not None and self._thread.is_alive()
        return LocalWorkerStatus(
            enabled=running,
            registered=cfg is not None,
            name=cfg.name if cfg else None,
            worker_id=cfg.worker_id if cfg else None,
            policy=self._policy,
            available=avail.available,
            reason=avail.reason,
            log=list(self._log),
        )

    def _register(self) -> WorkerConfig:
        status = self.ws.status()
        if self.ws.workspace_id is None or status.server_url is None:
            raise BackendError(409, "log in and pick a workspace first")
        old = load_config(self.path)
        name = socket.gethostname().removesuffix(".local")
        reg = self.ws.client().register_worker(self.ws.workspace_id, name)
        if old is not None:  # the computer moved to another workspace: drop the old worker
            with contextlib.suppress(BackendError, OfflineError):
                self.ws.client().remove_worker(old.worker_id)
        cfg = WorkerConfig(
            status.server_url,
            reg.token,
            reg.worker.id,
            name,
            self.ws.workspace_id,
            self._policy.model_dump(mode="json"),
        )
        save_config(cfg, self.path)
        return cfg

    def update(self, req: LocalWorkerUpdate) -> LocalWorkerStatus:
        with self._lock:
            self._policy = req.policy
            cfg = self._config()
            if cfg is not None:
                policy = req.policy.model_dump(mode="json")
                save_config(dataclasses.replace(cfg, policy=policy), self.path)
            if req.enabled and not (self._thread and self._thread.is_alive()):
                cfg = cfg or self._register()
                self._start(cfg)
            elif not req.enabled:
                self.stop()
        return self.status()

    def _start(self, cfg: WorkerConfig) -> None:
        self._stop = threading.Event()
        client = BackendClient(cfg.server, access=cfg.token, factory=self.factory)
        stop = self._stop

        def loop() -> None:
            try:
                run_worker(
                    client,
                    stop=stop,
                    out=self._log.append,
                    policy=lambda: self._policy,
                    probe=self.probe,
                )
            except (BackendError, OfflineError) as e:
                self._log.append(f"worker stopped: {e}")

        self._thread = threading.Thread(target=loop, name="team-worker", daemon=True)
        self._thread.start()

    def stop(self, wait_s: float = 0.0) -> None:
        self._stop.set()
        if self._thread is not None and wait_s > 0:
            self._thread.join(wait_s)
