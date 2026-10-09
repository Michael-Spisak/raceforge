"""Team relay and run log for the live car link (spec 0027).

While the engine relays a car (``car_link``), :class:`LiveShare` publishes the stream to the team
backend (telemetry at most ``rate_hz``, everything else always) and :class:`RunRecorder` keeps every
message at full rate; when the link closes the recording is saved as a ``run`` object through the
sync client (offline-first, so it syncs later if the backend is down). Sharing never affects the car
link: any backend error just turns sharing off.
"""

import asyncio
import contextlib
import gzip
import json
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import websockets

from raceforge.backend.models import LiveSession
from raceforge.workspace.client import BackendError, OfflineError
from raceforge.workspace.sync import Workspace

RETRY_S = 10.0


class Socket(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


Connector = Callable[[str, dict[str, str]], Awaitable[Socket]]


async def _connect(url: str, headers: dict[str, str]) -> Socket:
    return await websockets.connect(url, additional_headers=headers, open_timeout=5)


def ws_url(server_url: str, path: str) -> str:
    base = server_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[8:]
    elif base.startswith("http://"):
        base = "ws://" + base[7:]
    return f"{base}/api/v1{path}"


def _auth(ws: Workspace) -> tuple[str, dict[str, str]] | None:
    """Server URL and a fresh bearer header, or None when not logged in / no workspace."""
    status = ws.status()
    if not status.logged_in or ws.workspace_id is None or status.server_url is None:
        return None
    client = ws.client()
    client.me()  # refreshes an expired access token
    return status.server_url, {"Authorization": f"Bearer {client.access}"}


class LiveShare:
    """Publishes one car link to the backend. States: off | sharing | offline."""

    def __init__(self, ws: Workspace, rate_hz: float = 10.0, connect: Connector = _connect) -> None:
        self.ws = ws
        self.period = 1.0 / max(1.0, min(20.0, rate_hz))
        self.connect = connect
        self.sock: Socket | None = None
        self.session: str | None = None
        self.state = "off"
        self._last_frame = 0.0
        self._next_try = 0.0
        self._car = "car"

    async def open(self, car: str) -> str:
        self._car = car
        self._next_try = time.monotonic() + RETRY_S
        try:
            auth = await asyncio.to_thread(_auth, self.ws)
            if auth is None:
                self.state = "off"
                return self.state
            server, headers = auth
            url = ws_url(server, f"/workspaces/{self.ws.workspace_id}/live/publish")
            self.sock = await self.connect(url, headers)
            await self.sock.send(json.dumps({"type": "start", "car": car}))
            reply = json.loads(await self.sock.recv())
            self.session = str(reply.get("id")) if isinstance(reply, dict) else None
            self.state = "sharing"
        except (
            OSError,
            TimeoutError,
            ValueError,
            BackendError,
            OfflineError,
            websockets.WebSocketException,
        ):
            self.state, self.sock = "offline", None
        return self.state

    async def send(self, text: str, kind: str | None) -> None:
        if self.sock is None:
            if self.state == "offline" and time.monotonic() >= self._next_try:
                await self.open(self._car)  # try again now and then
            if self.sock is None:
                return
        if kind == "telemetry":
            now = time.monotonic()
            if now - self._last_frame < self.period:
                return
            self._last_frame = now
        try:
            await self.sock.send(text)
        except (OSError, websockets.WebSocketException):
            self.state, self.sock = "offline", None
            self._next_try = time.monotonic() + RETRY_S

    async def close(self) -> None:
        if self.sock is not None:
            with contextlib.suppress(Exception):
                await self.sock.close()
        self.sock = None

    def message(self) -> dict[str, Any]:
        return {"type": "share", "state": self.state, "session": self.session}


class RunRecorder:
    """Every car/engine message at full rate as gzip JSONL, saved as a ``run`` object at the end."""

    def __init__(self, folder: Path | None = None) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="raceforge-live-", dir=folder))
        self.path = self.dir / "telemetry.jsonl.gz"
        self._f = gzip.open(self.path, "wt", encoding="utf-8")  # noqa: SIM115 (closed in save)
        self.frames = 0
        self.notes: list[dict[str, Any]] = []
        self.car = "car"
        self.started = datetime.now()
        self._t0 = time.monotonic()

    def add(self, msg: dict[str, Any]) -> None:
        if msg.get("type") == "telemetry":
            self.frames += 1
        elif msg.get("type") == "hello":
            self.car = str(msg.get("car") or self.car)
        self._f.write(json.dumps({"rx_t": round(time.time(), 3), "msg": msg}) + "\n")

    def note(self, text: str) -> None:
        self.notes.append({"rx_t": round(time.time(), 3), "text": text})

    def save(self, ws: Workspace) -> str | None:
        """Closes the file; saves it as a run version if it has frames. Returns the slug."""
        self._f.close()
        if self.frames == 0 or ws.workspace_id is None:
            shutil.rmtree(self.dir, ignore_errors=True)
            return None
        notes = self.dir / "notes.json"
        notes.write_text(json.dumps(self.notes, indent=1), encoding="utf-8")
        car = "".join(c if c.isalnum() else "-" for c in self.car.lower()).strip("-") or "car"
        slug = f"live-{car}-{self.started:%Y%m%d-%H%M%S}"
        minutes = (time.monotonic() - self._t0) / 60
        message = f"test drive {self.car}, {minutes:.1f} min, {self.frames} frames"
        ws.save_files("run", slug, [self.path, notes], message, entry=self.path.name)
        shutil.rmtree(self.dir, ignore_errors=True)  # the bytes are in the workspace blob cache now
        return slug


def list_sessions(ws: Workspace) -> list[LiveSession]:
    if ws.workspace_id is None:
        raise BackendError(409, "log in and pick a workspace first")
    r = ws.client().request("GET", f"/workspaces/{ws.workspace_id}/live")
    return [LiveSession.model_validate(x) for x in r.json()]


async def watch(ws: Workspace, session_id: str, connect: Connector = _connect) -> Socket:
    auth = await asyncio.to_thread(_auth, ws)
    if auth is None:
        raise BackendError(409, "log in and pick a workspace first")
    server, headers = auth
    return await connect(ws_url(server, f"/live/{session_id}/watch"), headers)
