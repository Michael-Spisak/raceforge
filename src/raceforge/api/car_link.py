"""Live link to a real car (spec 0010 B): relays the car runtime's WebSocket (spec 0005) to the UI.

The UI opens ``/api/v1/car/live`` and sends ``{"type": "connect", "url": "ws://car:8765", "token":
...}``. From then on every message from the car is forwarded unchanged, and the UI's ``teleop`` /
``teleop_release`` / ``stop`` / ``note`` messages go to the car. The engine adds ``{"type": "link",
"state": ..., "rtt_ms": ...}`` messages (connection state, round-trip time from WebSocket pings) so
the UI can warn above 100 ms.

Spec 0027: with ``share`` (default) and a logged-in workspace the stream is also published to the
team backend and recorded as a run log (``live_share``); ``{"type": "share", ...}`` messages tell
the UI whether sharing works. Sharing never interferes with the car link.
"""

import asyncio
import base64
import contextlib
import json
import time
from collections.abc import Callable
from typing import Any, Literal, cast
from urllib.parse import urlencode, urlsplit, urlunsplit

import segno  # pyright: ignore[reportMissingTypeStubs]
import websockets
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from raceforge.api.live_share import LiveShare, RunRecorder
from raceforge.api.models import CarPairingCode, CarPairingRequest
from raceforge.workspace.sync import Workspace

FORWARD_TO_CAR = {"teleop", "teleop_release", "stop", "note", "radio_check"}  # 0030: radio_check
PING_PERIOD_S = 1.0
CONNECT_TIMEOUT_S = 5.0


class CarConnect(BaseModel):
    type: Literal["connect"] = "connect"
    url: str
    token: str | None = None
    share: bool = True  # spec 0027: publish to the team backend when logged in
    share_rate_hz: float = Field(default=10.0, ge=1.0, le=20.0)


def car_url(url: str, token: str | None) -> str:
    """`ws://host:port` (scheme optional) plus `?token=` when given."""
    if "://" not in url:
        url = "ws://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("ws", "wss") or not parts.hostname:
        raise ValueError(f"not a car address: {url!r} (expected ws://host:port)")
    query = urlencode({"token": token}) if token else parts.query
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", query, ""))


async def _send(ui: WebSocket, msg: dict[str, Any]) -> None:
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await ui.send_text(json.dumps(msg))


async def relay(
    ui: WebSocket,
    workspace: Callable[[], Workspace | None] | None = None,
    share_factory: Callable[[Workspace, float], LiveShare] = LiveShare,
) -> None:
    """Runs one UI ↔ car session until either side closes."""
    await ui.accept()
    try:
        req = CarConnect.model_validate_json(await ui.receive_text())
        url = car_url(req.url, req.token)
    except (ValidationError, ValueError, WebSocketDisconnect) as exc:
        await _send(ui, {"type": "link", "state": "error", "detail": str(exc)})
        await ui.close()
        return
    await _send(ui, {"type": "link", "state": "connecting"})
    try:
        car = await asyncio.wait_for(
            websockets.connect(url, open_timeout=CONNECT_TIMEOUT_S, ping_interval=None),
            CONNECT_TIMEOUT_S + 1,
        )
    except (OSError, TimeoutError, websockets.WebSocketException) as exc:
        detail = str(exc) or type(exc).__name__
        await _send(
            ui, {"type": "link", "state": "error", "detail": f"car not reachable: {detail}"}
        )
        await ui.close()
        return
    await _send(ui, {"type": "link", "state": "connected"})
    ws = workspace() if workspace else None
    recorder = RunRecorder() if ws is not None and ws.workspace_id else None
    share = share_factory(ws, req.share_rate_hz) if ws is not None and req.share else None
    opened = False
    if share is None:
        await _send(ui, {"type": "share", "state": "off", "session": None})

    async def car_to_ui() -> None:
        nonlocal opened
        async for message in car:
            text = message if isinstance(message, str) else message.decode()
            await ui.send_text(text)
            try:
                msg: Any = json.loads(text)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            msg = cast(dict[str, Any], msg)
            kind = str(msg.get("type"))
            if recorder is not None:
                recorder.add(msg)
            if share is None:
                continue
            before = None if not opened else share.state
            if not opened:  # share under the car's name (hello), else its host name
                opened = True
                car_name = msg.get("car") if kind == "hello" else None
                await share.open(str(car_name or urlsplit(url).hostname or "car"))
            await share.send(text, kind)
            if share.state != before:
                await _send(ui, share.message())

    async def ui_to_car() -> None:
        while True:
            text = await ui.receive_text()
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("type") in FORWARD_TO_CAR:  # pyright: ignore[reportUnknownMemberType]
                await car.send(text)
                if recorder is not None and msg.get("type") == "note":  # pyright: ignore[reportUnknownMemberType]
                    recorder.note(str(msg.get("text", "")))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

    async def ping() -> None:
        while True:
            t0 = time.perf_counter()
            pong = await car.ping()
            await asyncio.wait_for(pong, 5)
            rtt = (time.perf_counter() - t0) * 1000
            await _send(ui, {"type": "link", "state": "connected", "rtt_ms": round(rtt, 1)})
            await asyncio.sleep(PING_PERIOD_S)

    tasks = [asyncio.create_task(c()) for c in (car_to_ui, ui_to_car, ping)]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    reason = "closed"
    for t in done:
        exc = t.exception()
        if exc is not None and not isinstance(exc, WebSocketDisconnect):
            reason = (
                f"car link lost: {exc}"
                if not isinstance(exc, TimeoutError)
                else "car stopped answering"
            )
    with contextlib.suppress(Exception):
        await car.close()
    if share is not None:
        await share.close()
    if recorder is not None and ws is not None:
        try:
            run = await asyncio.to_thread(recorder.save, ws)
        except Exception as exc:  # never lose the drive: the file stays on disk
            await _send(ui, {"type": "run", "saved": None, "detail": f"{exc} ({recorder.path})"})
        else:
            if run:
                await _send(ui, {"type": "run", "saved": run})
    await _send(ui, {"type": "link", "state": "closed", "detail": reason})
    with contextlib.suppress(RuntimeError):
        await ui.close()


def pairing_code(req: CarPairingRequest) -> CarPairingCode:
    """QR code for TrackScout's drive mode: the phone connects to the car directly (spec 0010 C)."""
    car_url(req.url, req.token)  # validates the address
    payload = json.dumps({"url": req.url, "token": req.token})
    data = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    code = f"raceforge://car?v=1&d={data}"
    return CarPairingCode(
        code=code, qr_svg=segno.make(code, error="m").svg_inline(scale=4, border=2)
    )
