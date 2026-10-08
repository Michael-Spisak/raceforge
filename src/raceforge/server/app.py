"""FastAPI app: REST + WebSocket engine API, LDraw files and the built frontend (spec 0008)."""

import asyncio
import contextlib
import time
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from raceforge import __version__
from raceforge.api.models import (
    ControllerInfo,
    CorridorResponse,
    ErrorMessage,
    Health,
    PartSummary,
    QuickstartResponse,
    QuickstartSchema,
    ReplayRequest,
    ReplaySummary,
    SimControl,
    SimProtocol,
    SimStart,
)
from raceforge.api.service import Engine
from raceforge.api.sim_session import SimSession
from raceforge.construct.quickstart import QuickStartParams
from raceforge.parts.ldraw import library_dir
from raceforge.track.procedural import CorridorParams

FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
FRAME_DT = 1 / 30


def create_app(engine: Engine | None = None, frontend_dist: Path | None = FRONTEND_DIST) -> FastAPI:
    eng = engine or Engine()
    app = FastAPI(title="RaceForge engine", version=__version__)
    app.add_middleware(  # the dev server (Vite) runs on another port
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health")
    def health() -> Health:
        return eng.health()

    @app.get("/api/v1/parts")
    def parts(query: str = "", category: str = "") -> list[PartSummary]:
        return eng.parts(query, category)

    @app.get("/api/v1/quickstart/schema")
    def quickstart_schema() -> QuickstartSchema:
        return eng.quickstart_schema()

    @app.post("/api/v1/quickstart")
    def quickstart(params: QuickStartParams) -> QuickstartResponse:
        return eng.quickstart(params)

    @app.post("/api/v1/quickstart/export/{kind}", response_class=PlainTextResponse)
    def export(kind: str, params: QuickStartParams) -> PlainTextResponse:
        try:
            filename, text = eng.export(params, kind)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return PlainTextResponse(
            text, headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )

    @app.get("/api/v1/controllers")
    def controllers(extra: Annotated[list[str] | None, Query()] = None) -> list[ControllerInfo]:
        return eng.controllers([Path(p) for p in extra or []])

    @app.post("/api/v1/corridor")
    def corridor(params: CorridorParams) -> CorridorResponse:
        return eng.corridor(params)

    @app.post("/api/v1/replays")
    def replay(req: ReplayRequest) -> ReplaySummary:
        path = Path(req.path)
        if not path.is_file():
            raise HTTPException(404, f"no such file: {path}")
        return eng.replay(path)

    @app.get("/api/v1/sim/protocol")
    def sim_protocol() -> SimProtocol:
        """Message types of the /api/v1/sim WebSocket (documentation only)."""
        return SimProtocol()

    @app.websocket("/api/v1/sim")
    async def sim_socket(ws: WebSocket) -> None:
        await ws.accept()
        try:
            start = SimStart.model_validate(await ws.receive_json())
            session = await asyncio.to_thread(SimSession, eng, start)
        except (ValidationError, OSError, ImportError, ValueError) as exc:
            await ws.send_json(ErrorMessage(message=str(exc)).model_dump(mode="json"))
            await ws.close()
            return
        await ws.send_json((await asyncio.to_thread(session.scene)).model_dump(mode="json"))
        speed, paused = start.speed, False
        control_dt = session.sim.control_dt
        try:
            while not session.done:
                with contextlib.suppress(TimeoutError):
                    msg = SimControl.model_validate(
                        await asyncio.wait_for(ws.receive_json(), 0.001)
                    )
                    if msg.type == "stop":
                        break
                    paused = {"pause": True, "resume": False}.get(msg.type, paused)
                    if msg.type == "speed" and msg.speed:
                        speed = msg.speed
                tick = time.perf_counter()
                if not paused:
                    if speed >= 100:  # as fast as possible: compute for most of a frame
                        deadline = tick + FRAME_DT * 0.9
                        while time.perf_counter() < deadline and not session.done:
                            await asyncio.to_thread(session.advance, 5)
                    else:
                        await asyncio.to_thread(
                            session.advance, max(1, round(speed * FRAME_DT / control_dt))
                        )
                await ws.send_json((await asyncio.to_thread(session.frame)).model_dump(mode="json"))
                await asyncio.sleep(max(0.0, FRAME_DT - (time.perf_counter() - tick)))
            await ws.send_json((await asyncio.to_thread(session.result)).model_dump(mode="json"))
            await ws.close()
        except WebSocketDisconnect:
            await asyncio.to_thread(session.result)

    ldraw = library_dir()
    if ldraw.is_dir():
        app.mount("/ldraw", StaticFiles(directory=ldraw), name="ldraw")
    if frontend_dist is not None and frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="ui")
    return app
