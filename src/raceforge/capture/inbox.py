"""TrackScout inbox and laptop relay (spec 0007 scope 9).

Passes that arrive from a phone (cable or Bluetooth) are stored here first. The relay then uploads
them to the team backend when the connection is good enough; otherwise they wait ("waiting for
upload") until the user chooses *upload now* / *upload when faster* / *keep only on this laptop*, or
a later check finds a faster connection. The upload itself is the resumable blob upload of spec
0006, so parts that reached the server are never sent twice.
"""

import hashlib
import json
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from raceforge.workspace.client import BackendError, OfflineError
from raceforge.workspace.sync import Workspace

State = Literal["receiving", "waiting", "uploading", "uploaded", "local_only"]
Policy = Literal["auto", "now", "keep"]
Source = Literal["usb", "bluetooth"]

MAX_ETA_S = 600.0  # slower than this (estimated) → ask, like the phone does
MIN_RATE_BPS = 1_000_000.0
RETRY_S = 300.0


class OfferedPass(BaseModel):
    """One pass as the phone offers it (outbox.json entry / Bluetooth OFFER)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    size: int
    sha256: str
    project: str
    pass_type: str = ""
    created_at: str = ""
    tag: str
    file: str | None = None


class InboxPass(BaseModel):
    id: str
    project: str
    pass_type: str
    created_at: str
    size: int
    sha256: str
    received: int = 0
    source: Source
    state: State = "receiving"
    policy: Policy = "auto"
    slug: str
    version: str | None = None
    rate_bps: float | None = None
    eta_s: float | None = None
    note: str = ""
    last_try: float = 0.0


def capture_slug(project: str) -> str:
    """Same rule as TrackScout's `captureSlug`: scan-<ascii-lowercase-name>."""
    ascii_name = unicodedata.normalize("NFKD", project).encode("ascii", "ignore").decode().lower()
    core = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-") or "track"
    return f"scan-{core}"[:63].rstrip("-")


def _safe_id(pass_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", pass_id):
        raise ValueError(f"invalid pass id {pass_id!r}")
    return pass_id


class Inbox:
    """Received passes on disk: ``<id>.tscan.part`` while receiving, then ``<id>.tscan``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._index = root / "index.json"

    def _load(self) -> dict[str, InboxPass]:
        if not self._index.exists():
            return {}
        raw = json.loads(self._index.read_text(encoding="utf-8"))
        return {k: InboxPass.model_validate(v) for k, v in raw.items()}

    def _save(self, items: dict[str, InboxPass]) -> None:
        tmp = self._index.with_suffix(".tmp")
        data = {k: v.model_dump(mode="json") for k, v in items.items()}
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self._index)

    def list(self) -> list[InboxPass]:
        with self._lock:
            return sorted(self._load().values(), key=lambda p: p.created_at, reverse=True)

    def get(self, pass_id: str) -> InboxPass:
        with self._lock:
            items = self._load()
            if pass_id not in items:
                raise KeyError(pass_id)
            return items[pass_id]

    def update(self, pass_id: str, **changes: object) -> InboxPass:
        with self._lock:
            items = self._load()
            items[pass_id] = items[pass_id].model_copy(update=changes)
            self._save(items)
            return items[pass_id]

    def part_path(self, pass_id: str) -> Path:
        return self.root / f"{_safe_id(pass_id)}.tscan.part"

    def path(self, pass_id: str) -> Path:
        return self.root / f"{_safe_id(pass_id)}.tscan"

    def begin(self, offer: OfferedPass, source: Source) -> int:
        """Register an offered pass; returns how many bytes we already have (resume offset)."""
        with self._lock:
            items = self._load()
            known = items.get(offer.id)
            if known is not None and known.state != "receiving":
                return known.size
            part = self.part_path(offer.id)
            have = part.stat().st_size if part.exists() else 0
            if known is None or known.sha256 != offer.sha256 or have > offer.size:
                part.unlink(missing_ok=True)
                have = 0
            items[offer.id] = InboxPass(
                id=offer.id,
                project=offer.project,
                pass_type=offer.pass_type,
                created_at=offer.created_at or datetime.now(UTC).isoformat(),
                size=offer.size,
                sha256=offer.sha256,
                received=have,
                source=source,
                slug=capture_slug(offer.project),
            )
            self._save(items)
            return have

    def append(self, pass_id: str, data: bytes) -> None:
        with self.part_path(pass_id).open("ab") as f:
            f.write(data)

    def complete(self, pass_id: str) -> InboxPass:
        """Verify the received file and move it out of ``.part``; a corrupt file is discarded."""
        with self._lock:
            item = self.get(pass_id)
            if item.state != "receiving":
                return item
            part = self.part_path(pass_id)
            digest = hashlib.sha256()
            with part.open("rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    digest.update(block)
            if digest.hexdigest() != item.sha256:
                part.unlink()
                self.update(pass_id, received=0)
                raise ValueError(f"pass {pass_id}: checksum mismatch, discarded")
            part.replace(self.path(pass_id))
            return self.update(pass_id, received=item.size, state="waiting")


class TooSlowError(Exception):
    def __init__(self, rate_bps: float, eta_s: float) -> None:
        super().__init__(f"{rate_bps / 1e6:.2f} MB/s, about {eta_s / 60:.0f} min")
        self.rate_bps = rate_bps
        self.eta_s = eta_s


class Relay:
    """Uploads inbox passes into the current workspace's capture object (scan-<project>)."""

    def __init__(
        self,
        ws: Workspace,
        inbox: Inbox,
        max_eta_s: float = MAX_ETA_S,
        min_rate_bps: float = MIN_RATE_BPS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ws = ws
        self.inbox = inbox
        self.max_eta_s = max_eta_s
        self.min_rate_bps = min_rate_bps
        self.clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def choose(
        self, pass_id: str, action: Literal["upload_now", "when_faster", "keep_local"]
    ) -> InboxPass:
        """The three choices of the Team tab."""
        item = self.inbox.get(pass_id)
        if item.state in ("receiving", "uploaded"):
            return item
        if action == "keep_local":
            return self.inbox.update(pass_id, policy="keep", state="local_only", note="")
        policy: Policy = "now" if action == "upload_now" else "auto"
        self.inbox.update(pass_id, policy=policy, state="waiting")
        return self.upload(pass_id)

    def upload(self, pass_id: str) -> InboxPass:
        with self._lock:
            item = self.inbox.get(pass_id)
            if item.state not in ("waiting",) or item.policy == "keep":
                return item
            if self.ws.workspace_id is None:
                return self.inbox.update(
                    pass_id, note="no workspace selected", last_try=self.clock()
                )
            path = self.inbox.path(pass_id)
            self.inbox.update(pass_id, state="uploading", note="", last_try=self.clock())
            start = self.clock()

            def check(done: int, total: int, sent: int) -> None:
                elapsed = max(self.clock() - start, 1e-6)
                rate = sent / elapsed
                eta = (total - done) / rate if rate > 0 else float("inf")
                self.inbox.update(pass_id, received=item.size, rate_bps=rate, eta_s=eta)
                slow = eta > self.max_eta_s or rate < self.min_rate_bps
                if item.policy == "auto" and done < total and slow:
                    raise TooSlowError(rate, eta)

            try:
                self.ws.client().upload_blob(path, item.sha256, progress=check)
                version = self.ws.save_files(
                    "capture", item.slug, [path], f"{item.pass_type} pass via laptop", merge=True
                )
                self.ws.sync()
            except TooSlowError as slow:
                return self.inbox.update(
                    pass_id, state="waiting", rate_bps=slow.rate_bps, eta_s=slow.eta_s, note="slow"
                )
            except OfflineError:
                return self.inbox.update(pass_id, state="waiting", note="offline")
            except BackendError as exc:
                return self.inbox.update(pass_id, state="waiting", note=exc.detail[:200])
            semver = next(
                (v.semver for v in self.ws.history(version.object_id) if v.id == version.id),
                version.semver,
            )
            path.unlink(missing_ok=True)  # the blob cache of the workspace has it now
            return self.inbox.update(pass_id, state="uploaded", version=semver, note="")

    def tick(self) -> None:
        """Background check: retry waiting passes with policy ``auto`` every ``RETRY_S``."""
        for item in self.inbox.list():
            due = item.policy == "now" or self.clock() - item.last_try >= RETRY_S
            if item.state == "waiting" and item.policy != "keep" and due:
                with suppress(OSError):
                    self.upload(item.id)

    def start_background(self, interval_s: float = 60.0) -> None:
        def loop() -> None:
            while not self._stop.wait(interval_s):
                self.tick()

        threading.Thread(target=loop, name="trackscout-relay", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
