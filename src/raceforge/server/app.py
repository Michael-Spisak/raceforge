"""FastAPI app: REST + WebSocket engine API, LDraw files and the built frontend (spec 0008)."""

import asyncio
import contextlib
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from raceforge import __version__
from raceforge.api import deploy as car_deploy
from raceforge.api.car_link import pairing_code
from raceforge.api.car_link import relay as car_relay
from raceforge.api.construct_settings import load_settings as load_construct_settings
from raceforge.api.construct_settings import save_settings as save_construct_settings
from raceforge.api.models import (
    AssemblyEditRequest,
    AssemblyEditResponse,
    AssemblyExportRequest,
    BundleInfo,
    BundleRequest,
    CarPairingCode,
    CarPairingRequest,
    ConnectorDef,
    ConstructSettings,
    ControllerInfo,
    CorridorResponse,
    DeployRequest,
    DeployResponse,
    ErrorMessage,
    Health,
    InboxAction,
    InviteRequest,
    LDrawPart,
    LocalPartRequest,
    PartSummary,
    PrintedImportRequest,
    PrintedPreview,
    QuickstartResponse,
    QuickstartSchema,
    QuickTrackInfo,
    QuickTrackPreview,
    ReceiveRequest,
    RecordingInfo,
    ReplayRequest,
    ReplaySummary,
    SaveAssembly,
    SaveFiles,
    SaveQuickstart,
    ScanDetail,
    ScanMesh,
    ScanOpen,
    ScanPassRef,
    ScanTrack,
    SimControl,
    SimProtocol,
    SimStart,
    TeamJobRequest,
    TokenRequest,
    TrackScoutPairing,
    TrainBCRequest,
    TrainBenchRequest,
    TrainJob,
    TrainRLRequest,
    TrainTuneRequest,
    WorkspaceLogin,
    WorkspaceName,
    WorkspaceRegister,
    WorkspaceSelect,
)
from raceforge.api.scans import ScanApi, ScanNotFoundError
from raceforge.api.service import Engine
from raceforge.api.sim_session import SimSession
from raceforge.api.tracks import QuickTracks
from raceforge.api.tracks import preview as quick_preview
from raceforge.api.train_jobs import TrainJobs
from raceforge.api.workspace import WorkspaceApi
from raceforge.backend.models import (
    ApiTokenInfo,
    InviteInfo,
    JobInfo,
    TotpCode,
    TotpSetup,
    UserInfo,
    WorkerInfo,
    WorkspaceInfo,
)
from raceforge.capture.inbox import InboxPass
from raceforge.capture.rftx import TransferError
from raceforge.capture.tscan import TscanError
from raceforge.construct.quickstart import QuickStartParams
from raceforge.parts.ldraw import library_dir
from raceforge.track.procedural import CorridorParams
from raceforge.track.quick import QuickTrack
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
    train_jobs = TrainJobs()
    quick_tracks = QuickTracks()
    scan_holder: list[ScanApi] = []

    def scans() -> ScanApi:
        if not scan_holder:
            scan_holder.append(ScanApi(ws()))
        return scan_holder[0]

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

    @app.post("/api/v1/assembly/export/{kind}", response_class=PlainTextResponse)
    def assembly_export(kind: str, req: AssemblyExportRequest) -> PlainTextResponse:
        """Export the edited car: assembly | mpd | mjcf | bom (spec 0015)."""
        try:
            filename, text = eng.export_assembly(req.assembly, req.quickstart, kind)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(
                422 if kind in ("assembly", "mpd", "mjcf", "bom") else 404, str(exc)
            ) from exc
        return PlainTextResponse(
            text, headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )

    @app.get("/api/v1/parts/ldraw")
    def parts_ldraw(
        query: str = "", limit: Annotated[int, Query(ge=1, le=500)] = 50
    ) -> list[LDrawPart]:
        """Search the whole LDraw library (spec 0018)."""
        return eng.ldraw_search(query, limit)

    @app.post("/api/v1/parts/local")
    def parts_add_local(req: LocalPartRequest) -> PartSummary:
        """Add an LDraw part to the team's local catalogue (unverified)."""
        try:
            return eng.add_local_part(req)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/parts/{key}/connectors")
    def part_connectors(key: str) -> list[ConnectorDef]:
        """Connectors of a part in its own frame (spec 0021)."""
        try:
            return eng.part_connectors(key)
        except KeyError as exc:
            raise HTTPException(404, f"no part {key!r}") from exc

    @app.put("/api/v1/parts/{key}/connectors")
    def part_set_connectors(key: str, defs: list[ConnectorDef]) -> list[ConnectorDef]:
        """Replace the connectors of a 3D-printed part (clicked on its mesh, spec 0021)."""
        try:
            return eng.set_printed_connectors(key, defs)
        except KeyError as exc:
            raise HTTPException(404, f"no part {key!r}") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/v1/parts/printed/preview")
    def parts_printed_preview(req: PrintedImportRequest) -> PrintedPreview:
        """Volume, size, mass and cost of a mesh before it is imported (spec 0019)."""
        try:
            return eng.printed_preview(req)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/v1/parts/printed")
    def parts_printed_import(req: PrintedImportRequest) -> PartSummary:
        """Import a 3D-printed part into the team's local catalogue (spec 0019)."""
        try:
            return eng.import_printed(req)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/parts/printed/{key}/mesh")
    def parts_printed_mesh(key: str) -> FileResponse:
        try:
            path = eng.mesh_path(key)
        except KeyError as exc:
            raise HTTPException(404, f"no printed part {key!r}") from exc
        if not path.is_file():
            raise HTTPException(404, f"mesh of {key!r} is missing")
        return FileResponse(path, media_type="model/stl")

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

    @app.post("/api/v1/car/pairing-code")
    def car_pairing_code(req: CarPairingRequest) -> CarPairingCode:
        """QR code that pairs TrackScout's drive mode with a car (spec 0010 C)."""
        try:
            return pairing_code(req)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/v1/car/bundle")
    def car_bundle(req: BundleRequest) -> BundleInfo:
        """Build a test-mode deploy bundle (spec 0012)."""
        try:
            return car_deploy.build_from_request(req)
        except car_deploy.BundleError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/v1/car/deploy")
    def car_deploy_bundle(req: DeployRequest) -> DeployResponse:
        """Install a bundle over SSH or write it to a USB stick (spec 0012)."""
        try:
            return car_deploy.deploy_from_request(req)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except car_deploy.DeployError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/v1/car/deploy/usb-result")
    def car_usb_result(stick: str) -> car_deploy.InstallResult | None:
        """What the car wrote back to the stick (null: not plugged into a car yet)."""
        try:
            return car_deploy.usb_result(Path(stick).expanduser())
        except ValueError as exc:
            raise HTTPException(422, f"result.json on the stick is damaged: {exc}") from exc

    @app.websocket("/api/v1/car/live")
    async def car_live(ws: WebSocket) -> None:
        """Relay to a real car's telemetry/teleop WebSocket (spec 0010; protocol in car_link)."""
        await car_relay(ws)

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
                    elif msg.type == "teleop":
                        session.teleop.drive(msg.steer or 0.0, msg.speed or 0.0)
                        speed, paused = 1.0, False  # a human drives in real time
                    elif msg.type == "teleop_release":
                        session.teleop.release()
                    elif msg.type == "stop_car":
                        session.teleop.stop()
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

    # ---------------------------------------------------- team workers & jobs (spec 0020)
    @app.get(f"{w}/workers")
    def ws_workers() -> list[WorkerInfo]:
        return ws().workers()

    @app.get(f"{w}/jobs")
    def ws_jobs() -> list[JobInfo]:
        return ws().jobs()

    @app.post(f"{w}/jobs")
    def ws_submit_job(req: TeamJobRequest) -> JobInfo:
        """Queue a benchmark/tune for the team's workers (the controller file is sent along)."""
        try:
            return ws().submit_job(req)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post(f"{w}/jobs/{{job_id}}/cancel")
    def ws_cancel_job(job_id: str) -> JobInfo:
        return ws().cancel_job(job_id)

    @app.post(f"{w}/jobs/{{job_id}}/save-params")
    def ws_save_job_params(job_id: str, req: ScanOpen) -> ScanOpen:
        """Write a finished tune job's parameters to ``path``."""
        try:
            return ScanOpen(path=ws().save_job_params(job_id, req.path))
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc

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

    @app.post(f"{w}/save/assembly")
    def ws_save_assembly(req: SaveAssembly) -> LocalVersion:
        try:
            return ws().save_assembly(req)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

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

    @app.post(f"{w}/pair-trackscout")
    def ws_pair_trackscout() -> TrackScoutPairing:
        return ws().pair_trackscout()

    # ------------------------------------------------------------ scans (spec 0009)
    # ------------------------------------------------------------ assembly editor (spec 0015)
    @app.post("/api/v1/assembly/edit")
    def assembly_edit(req: AssemblyEditRequest) -> AssemblyEditResponse:
        """Apply one edit (move/rotate/delete/add/snap) and return the evaluated assembly."""
        try:
            return eng.edit_assembly(req)
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc).strip("'")) from exc

    @app.get("/api/v1/construct/settings")
    def construct_settings_get() -> ConstructSettings:
        """Part prices, size/weight limits and budget for the rule checker (spec 0016)."""
        return load_construct_settings()

    @app.put("/api/v1/construct/settings")
    def construct_settings_put(s: ConstructSettings) -> ConstructSettings:
        unknown = [k for k in s.prices if k not in eng.cat.entries]
        if unknown:
            raise HTTPException(422, f"unknown parts: {', '.join(unknown)}")
        return save_construct_settings(s)

    # ------------------------------------------------------------ quick tracks (spec 0014)
    @app.post("/api/v1/tracks/quick/preview")
    def quick_track_preview(q: QuickTrack) -> QuickTrackPreview:
        """2D preview of a drawn track as the simulator builds it (``ok: false`` + reason)."""
        return quick_preview(q)

    @app.get("/api/v1/tracks/quick")
    def quick_track_list() -> list[QuickTrackInfo]:
        return quick_tracks.list()

    @app.get("/api/v1/tracks/quick/{name}")
    def quick_track_get(name: str) -> QuickTrack:
        try:
            return quick_tracks.get(name)
        except KeyError as exc:
            raise HTTPException(404, f"no quick track {name!r}") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.put("/api/v1/tracks/quick/{name}")
    def quick_track_save(name: str, q: QuickTrack) -> QuickTrackPreview:
        try:
            return quick_tracks.save(name, q)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/v1/tracks/quick/{name}")
    def quick_track_delete(name: str) -> None:
        try:
            quick_tracks.delete(name)
        except KeyError as exc:
            raise HTTPException(404, f"no quick track {name!r}") from exc

    # ------------------------------------------------------------ training (spec 0013)
    @app.post("/api/v1/train/benchmark")
    def train_benchmark(req: TrainBenchRequest) -> TrainJob:
        """Start a benchmark job: the controller on held-out corridors."""
        try:
            return train_jobs.start_benchmark(req)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ImportError, OSError, ValueError, SyntaxError) as exc:
            raise HTTPException(422, f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/v1/train/tune")
    def train_tune(req: TrainTuneRequest) -> TrainJob:
        """Start an Optuna tuning job over the controller's Tunable params."""
        try:
            return train_jobs.start_tune(req)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ImportError, OSError, ValueError, SyntaxError) as exc:
            raise HTTPException(422, f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/v1/train/rl")
    def train_rl(req: TrainRLRequest) -> TrainJob:
        """Start PPO training (spec 0022); needs the optional extra ``rl``."""
        try:
            return train_jobs.start_rl(req)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/train/recordings")
    def train_recordings() -> list[RecordingInfo]:
        """Recorded runs (e.g. teleop demonstrations) in the engine's runs folder (spec 0023)."""
        return train_jobs.recordings()

    @app.post("/api/v1/train/bc")
    def train_bc(req: TrainBCRequest) -> TrainJob:
        """Learn a policy from recorded drives (behaviour cloning, spec 0023)."""
        try:
            return train_jobs.start_bc(req)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/train/jobs")
    def train_job_list() -> list[TrainJob]:
        return train_jobs.list()

    @app.get("/api/v1/train/jobs/{job_id}")
    def train_job(job_id: str) -> TrainJob:
        try:
            return train_jobs.get(job_id)
        except KeyError as exc:
            raise HTTPException(404, f"no job {job_id}") from exc

    @app.post("/api/v1/train/jobs/{job_id}/cancel")
    def train_job_cancel(job_id: str) -> TrainJob:
        try:
            return train_jobs.cancel(job_id)
        except KeyError as exc:
            raise HTTPException(404, f"no job {job_id}") from exc

    @app.get("/api/v1/scans")
    def scan_tracks() -> list[ScanTrack]:
        return scans().tracks()

    @app.post("/api/v1/scans/open")
    def scan_open(req: ScanOpen) -> ScanPassRef:
        try:
            return scans().open_file(req.path)
        except (TscanError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/scans/{sha256}")
    def scan_detail(sha256: str) -> ScanDetail:
        try:
            return scans().detail(sha256)
        except ScanNotFoundError as exc:
            raise HTTPException(404, f"no scan {sha256}") from exc
        except TscanError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/scans/{sha256}/mesh")
    def scan_mesh(
        sha256: str, max_faces: Annotated[int, Query(ge=1, le=5_000_000)] = 300_000
    ) -> ScanMesh:
        try:
            return scans().mesh(sha256, max_faces)
        except ScanNotFoundError as exc:
            raise HTTPException(404, f"no scan {sha256}") from exc
        except TscanError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get(f"{w}/trackscout/inbox")
    def ws_trackscout_inbox() -> list[InboxPass]:
        return ws().trackscout_inbox()

    @app.post(f"{w}/trackscout/inbox/{{pass_id}}")
    def ws_trackscout_choose(pass_id: str, req: InboxAction) -> InboxPass:
        try:
            return ws().trackscout_choose(pass_id, req)
        except KeyError as exc:
            raise HTTPException(404, f"no pass {pass_id}") from exc

    @app.post(f"{w}/trackscout/receive")
    async def ws_trackscout_receive(req: ReceiveRequest) -> list[InboxPass]:
        try:
            return await ws().trackscout_receive(req)
        except (TransferError, ConnectionError, RuntimeError, OSError, ValueError) as exc:
            raise HTTPException(502, str(exc)) from exc

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
