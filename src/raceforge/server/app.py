"""FastAPI app: REST + WebSocket engine API, LDraw files and the built frontend (spec 0008)."""

import asyncio
import contextlib
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from raceforge import __version__
from raceforge.api.models import (
    ControllerInfo,
    CorridorResponse,
    ErrorMessage,
    Health,
    InviteRequest,
    PartSummary,
    QuickstartResponse,
    QuickstartSchema,
    ReplayRequest,
    ReplaySummary,
    SaveFiles,
    SaveQuickstart,
    SimControl,
    SimProtocol,
    SimStart,
    TokenRequest,
    WorkspaceLogin,
    WorkspaceName,
    WorkspaceRegister,
    WorkspaceSelect,
)
from raceforge.api.service import Engine
from raceforge.api.sim_session import SimSession
from raceforge.api.workspace import WorkspaceApi
from raceforge.backend.models import (
    ApiTokenInfo,
    InviteInfo,
    TotpCode,
    TotpSetup,
    UserInfo,
    WorkspaceInfo,
)
from raceforge.construct.quickstart import QuickStartParams
from raceforge.parts.ldraw import library_dir
from raceforge.track.procedural import CorridorParams
from raceforge.workspace.client import BackendError, OfflineError
from raceforge.workspace.sync import (
    Conflict,
    LocalObject,
    LocalVersion,
    SyncResult,
    WorkspaceStatus,
)

FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
FRAME_DT = 1 / 30


def create_app(
    engine: Engine | None = None,
    frontend_dist: Path | None = FRONTEND_DIST,
    workspace: WorkspaceApi | None = None,
) -> FastAPI:
    eng = engine or Engine()
    app = FastAPI(title="RaceForge engine", version=__version__)
    holder: list[WorkspaceApi] = [workspace] if workspace else []

    def ws() -> WorkspaceApi:  # created on first use (opens ~/.cache/raceforge/workspace)
        if not holder:
            holder.append(WorkspaceApi(eng.cat))
        return holder[0]

    app.state.workspace = ws

    @app.exception_handler(BackendError)
    async def _backend_error(_r: object, exc: BackendError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    @app.exception_handler(OfflineError)
    async def _offline(_r: object, _exc: OfflineError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse({"detail": "backend not reachable (offline)"}, status_code=503)

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

    # ---- team workspace (spec 0006)
    w = "/api/v1/workspace"

    @app.get(f"{w}/status")
    def ws_status(probe: bool = False) -> WorkspaceStatus:
        return ws().status(probe)

    @app.post(f"{w}/login")
    def ws_login(req: WorkspaceLogin) -> WorkspaceStatus:
        return ws().login(req)

    @app.post(f"{w}/register")
    def ws_register(req: WorkspaceRegister) -> UserInfo:
        return ws().register(req)

    @app.post(f"{w}/logout")
    def ws_logout() -> WorkspaceStatus:
        return ws().logout()

    @app.get(f"{w}/workspaces")
    def ws_list() -> list[WorkspaceInfo]:
        return ws().workspaces()

    @app.post(f"{w}/workspaces")
    def ws_create(req: WorkspaceName) -> WorkspaceInfo:
        return ws().create_workspace(req.name)

    @app.post(f"{w}/select")
    def ws_select(req: WorkspaceSelect) -> WorkspaceStatus:
        return ws().select(req.workspace_id)

    @app.post(f"{w}/sync")
    def ws_sync() -> SyncResult:
        return ws().sync()

    @app.get(f"{w}/objects")
    def ws_objects(kind: str | None = None) -> list[LocalObject]:
        return ws().objects(kind)

    @app.get(f"{w}/objects/{{object_id}}/versions")
    def ws_history(object_id: str) -> list[LocalVersion]:
        return ws().history(object_id)

    @app.get(f"{w}/versions/{{version_id}}")
    def ws_version(version_id: str) -> dict[str, Any]:
        return ws().version(version_id)

    @app.get(f"{w}/conflicts")
    def ws_conflicts() -> list[Conflict]:
        return ws().conflicts()

    @app.post(f"{w}/save/quickstart")
    def ws_save_quickstart(req: SaveQuickstart) -> LocalVersion:
        return ws().save_quickstart(req)

    @app.post(f"{w}/save/files")
    def ws_save_files(req: SaveFiles) -> LocalVersion:
        try:
            return ws().save_files(req)
        except FileNotFoundError as exc:
            raise HTTPException(404, f"no such file: {exc}") from exc

    @app.post(f"{w}/totp/setup")
    def ws_totp_setup() -> TotpSetup:
        return ws().totp_setup()

    @app.post(f"{w}/totp/verify")
    def ws_totp_verify(req: TotpCode) -> UserInfo:
        return ws().totp_verify(req.code)

    @app.get(f"{w}/invites")
    def ws_invites() -> list[InviteInfo]:
        return ws().invites()

    @app.post(f"{w}/invites")
    def ws_invite(req: InviteRequest) -> InviteInfo:
        return ws().create_invite(req.role)

    @app.get(f"{w}/tokens")
    def ws_tokens() -> list[ApiTokenInfo]:
        return ws().tokens()

    @app.post(f"{w}/tokens")
    def ws_token(req: TokenRequest) -> ApiTokenInfo:
        return ws().create_token(req.name, list(req.scopes), req.client)

    @app.delete(f"{w}/tokens/{{token_id}}", status_code=204)
    def ws_revoke(token_id: str) -> None:
        ws().revoke_token(token_id)

    ldraw = library_dir()
    if ldraw.is_dir():
        app.mount("/ldraw", StaticFiles(directory=ldraw), name="ldraw")
    if frontend_dist is not None and frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="ui")
    return app
