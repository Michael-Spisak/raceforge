"""Live link to a real car (spec 0010 B): relays the car runtime's WebSocket (spec 0005) to the UI.

The UI opens ``/api/v1/car/live`` and sends ``{"type": "connect", "url": "ws://car:8765", "token":
...}``. From then on every message from the car is forwarded unchanged, and the UI's ``teleop`` /
``teleop_release`` / ``stop`` / ``note`` messages go to the car. The engine adds ``{"type": "link",
"state": ..., "rtt_ms": ...}`` messages (connection state, round-trip time from WebSocket pings) so
the UI can warn above 100 ms.
"""

import asyncio
import base64
import contextlib
import json
import time
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

import segno  # pyright: ignore[reportMissingTypeStubs]
import websockets
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from raceforge.api.models import CarPairingCode, CarPairingRequest

FORWARD_TO_CAR = {"teleop", "teleop_release", "stop", "note"}
PING_PERIOD_S = 1.0
CONNECT_TIMEOUT_S = 5.0


class CarConnect(BaseModel):
    type: Literal["connect"] = "connect"
    url: str
    token: str | None = None


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


async def relay(ui: WebSocket) -> None:
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

    async def car_to_ui() -> None:
        async for message in car:
            await ui.send_text(message if isinstance(message, str) else message.decode())

    async def ui_to_car() -> None:
        while True:
            text = await ui.receive_text()
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("type") in FORWARD_TO_CAR:  # pyright: ignore[reportUnknownMemberType]
                await car.send(text)

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
