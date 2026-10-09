"""FastAPI app of the team backend (spec 0006). Separate from the local engine API (spec 0008)."""

import asyncio
import contextlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    Header,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from raceforge import __version__
from raceforge.backend.blobs import make_store
from raceforge.backend.db import Database
from raceforge.backend.live import PUBLISHER_IDLE_S, LiveHub
from raceforge.backend.models import (
    ApiTokenCreate,
    ApiTokenInfo,
    AuditInfo,
    DraftIn,
    DraftOut,
    InviteCreate,
    InviteInfo,
    JobCreate,
    JobFinish,
    JobInfo,
    JobProgress,
    JobProgressAck,
    LiveSession,
    LoginRequest,
    ObjectCopy,
    ObjectCreate,
    ObjectInfo,
    ObjectUpdate,
    Problem,
    RefreshRequest,
    RegisterRequest,
    Scope,
    Status,
    TokenPair,
    TotpCode,
    TotpSetup,
    UploadCreate,
    UploadInfo,
    UserInfo,
    VersionContent,
    VersionCreate,
    VersionInfo,
    WorkerHeartbeat,
    WorkerInfo,
    WorkerJob,
    WorkerRegister,
    WorkerRegistration,
    WorkspaceCreate,
    WorkspaceInfo,
)
from raceforge.backend.service import Actor, ApiError, Backend
from raceforge.backend.settings import Settings
from raceforge.core.meta import ObjectKind

ACCESS_COOKIE, REFRESH_COOKIE = "rf_access", "rf_refresh"
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
ERRORS: dict[int | str, dict[str, object]] = {
    code: {"model": Problem} for code in (400, 401, 403, 404, 409, 422, 429)
}


_bearer = HTTPBearer(auto_error=False)


def _actor(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Actor:
    be: Backend = request.app.state.backend
    secret = creds.credentials if creds else request.cookies.get(ACCESS_COOKIE)
    if not secret:
        raise ApiError(401, "not authenticated")
    return be.authenticate(secret)


Auth = Annotated[Actor, Depends(_actor)]


def make_backend(
    settings: Settings | None = None, clock: Callable[[], datetime] | None = None
) -> Backend:
    cfg = settings or Settings.from_env()
    return Backend(
        cfg, Database(cfg.database_url), make_store(cfg), clock or (lambda: datetime.now(UTC))
    )


def _session_msg(info: LiveSession) -> str:
    return '{"type": "session", ' + info.model_dump_json()[1:]


def create_backend_app(backend: Backend | None = None) -> FastAPI:
    be = backend or make_backend()
    app = FastAPI(title="RaceForge backend", version=__version__, responses=ERRORS)
    app.state.backend = be

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    def set_cookies(resp: Response, pair: TokenPair) -> None:
        secure = be.settings.secure_cookies
        resp.set_cookie(
            ACCESS_COOKIE,
            pair.access_token,
            max_age=pair.expires_in,
            httponly=True,
            secure=secure,
            samesite="strict",
        )
        resp.set_cookie(
            REFRESH_COOKIE,
            pair.refresh_token,
            max_age=be.settings.refresh_ttl_s,
            httponly=True,
            secure=secure,
            samesite="strict",
            path="/api/v1/auth",
        )

    p = "/api/v1"

    # ---------------------------------------------------------------- auth & users
    @app.post(f"{p}/auth/login")
    def login(req: LoginRequest, request: Request, response: Response) -> TokenPair:
        ip = request.client.host if request.client else "?"
        pair = be.login(req, ip)
        set_cookies(response, pair)
        return pair

    @app.post(f"{p}/auth/refresh")
    def refresh(req: RefreshRequest, request: Request, response: Response) -> TokenPair:
        secret = req.refresh_token or request.cookies.get(REFRESH_COOKIE)
        if not secret:
            raise ApiError(401, "no refresh token")
        pair = be.refresh(secret)
        set_cookies(response, pair)
        return pair

    @app.post(f"{p}/auth/logout", status_code=204)
    def logout(a: Auth, response: Response) -> None:
        be.logout(a)
        response.delete_cookie(ACCESS_COOKIE)
        response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")

    @app.post(f"{p}/auth/totp/setup")
    def totp_setup(a: Auth) -> TotpSetup:
        return be.totp_setup(a)

    @app.post(f"{p}/auth/totp/verify")
    def totp_verify(req: TotpCode, a: Auth) -> UserInfo:
        return be.totp_verify(a, req.code)

    @app.post(f"{p}/auth/register", status_code=201)
    def register(req: RegisterRequest) -> UserInfo:
        return be.register(req)

    @app.get(f"{p}/users/me")
    def me(a: Auth) -> UserInfo:
        return be.me(a)

    @app.get(f"{p}/invites")
    def list_invites(a: Auth) -> list[InviteInfo]:
        return be.list_invites(a)

    @app.post(f"{p}/invites", status_code=201)
    def create_invite(req: InviteCreate, a: Auth) -> InviteInfo:
        return be.create_invite(a, req)

    @app.get(f"{p}/tokens")
    def list_tokens(a: Auth) -> list[ApiTokenInfo]:
        return be.list_tokens(a)

    @app.post(f"{p}/tokens", status_code=201)
    def create_token(req: ApiTokenCreate, a: Auth) -> ApiTokenInfo:
        return be.create_token(a, req)

    @app.delete(f"{p}/tokens/{{token_id}}", status_code=204)
    def revoke_token(token_id: str, a: Auth) -> None:
        be.revoke_token(a, token_id)

    # ---------------------------------------------------------------- live relay (spec 0027)
    hub = LiveHub()
    app.state.live = hub

    async def ws_actor(ws: WebSocket, scope: Scope) -> Actor | None:
        """Header (engine) or cookie auth; closes the socket and returns None if denied."""
        auth = ws.headers.get("authorization", "")
        bearer = auth.lower().startswith("bearer ")
        secret = auth[7:] if bearer else ws.cookies.get(ACCESS_COOKIE)
        try:
            if not secret:
                raise ApiError(401, "not authenticated")
            actor = await run_in_threadpool(be.authenticate, secret)
            be.require(actor, scope)
            return actor
        except ApiError as e:
            await ws.close(code=4000 + e.status, reason=e.detail[:120])
            return None

    @app.get(f"{p}/workspaces/{{workspace_id}}/live")
    def list_live(workspace_id: str, a: Auth) -> list[LiveSession]:
        be.require(a, Scope.READ)
        return hub.list(workspace_id)

    @app.websocket(f"{p}/workspaces/{{workspace_id}}/live/publish")
    async def live_publish(ws: WebSocket, workspace_id: str) -> None:
        actor = await ws_actor(ws, Scope.SIM_TRAIN)
        if actor is None:
            return
        ids = {w.id for w in await run_in_threadpool(be.list_workspaces, actor)}
        if workspace_id not in ids:
            await ws.close(code=4404, reason="workspace not found")
            return
        await ws.accept()
        try:
            start = await asyncio.wait_for(ws.receive_json(), PUBLISHER_IDLE_S)
            car = str(start.get("car") or "car") if isinstance(start, dict) else "car"
        except (TimeoutError, ValueError, WebSocketDisconnect):
            await ws.close()
            return
        session = hub.start(workspace_id, car, actor.username, be.clock())
        await ws.send_json({"type": "session", "id": session.id})
        try:
            while True:
                text = await asyncio.wait_for(ws.receive_text(), PUBLISHER_IDLE_S)
                hub.publish(session.id, text, be.clock())
        except (TimeoutError, WebSocketDisconnect):
            pass
        finally:
            hub.end(session.id)
            with contextlib.suppress(RuntimeError):
                await ws.close()

    @app.websocket(f"{p}/live/{{session_id}}/watch")
    async def live_watch(ws: WebSocket, session_id: str) -> None:
        if await ws_actor(ws, Scope.READ) is None:
            return
        info = hub.get(session_id)
        queue = hub.watch(session_id) if info else None
        if info is None or queue is None:
            await ws.close(code=4404, reason="no such live session (or too many watchers)")
            return
        await ws.accept()

        async def ignore_input() -> None:  # watchers are read-only; this notices a disconnect
            while True:
                await ws.receive_text()

        reader = asyncio.create_task(ignore_input())
        try:
            await ws.send_text(_session_msg(info))
            while not reader.done():
                get = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({get, reader}, return_when=asyncio.FIRST_COMPLETED)
                if get not in done:
                    get.cancel()
                    break
                text = get.result()
                if text is None:
                    await ws.send_text('{"type": "end"}')
                    break
                await ws.send_text(text)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            reader.cancel()
            hub.unwatch(session_id, queue)
            with contextlib.suppress(RuntimeError):
                await ws.close()

    # ---------------------------------------------------------------- workers & jobs (spec 0020)
    @app.post(f"{p}/workers", status_code=201)
    def register_worker(req: WorkerRegister, a: Auth) -> WorkerRegistration:
        """Register a team computer; the returned token only works for the /worker endpoints."""
        return be.register_worker(a, req)

    @app.get(f"{p}/workspaces/{{workspace_id}}/workers")
    def list_workers(workspace_id: str, a: Auth) -> list[WorkerInfo]:
        return be.list_workers(a, workspace_id)

    @app.delete(f"{p}/workers/{{worker_id}}", status_code=204)
    def remove_worker(worker_id: str, a: Auth) -> None:
        be.remove_worker(a, worker_id)

    @app.post(f"{p}/workspaces/{{workspace_id}}/jobs", status_code=201)
    def create_job(workspace_id: str, req: JobCreate, a: Auth) -> JobInfo:
        return be.create_job(a, workspace_id, req)

    @app.get(f"{p}/workspaces/{{workspace_id}}/jobs")
    def list_jobs(
        workspace_id: str, a: Auth, limit: Annotated[int, Query(ge=1, le=500)] = 50
    ) -> list[JobInfo]:
        return be.list_jobs(a, workspace_id, limit)

    @app.get(f"{p}/jobs/{{job_id}}")
    def get_job(job_id: str, a: Auth) -> JobInfo:
        return be.get_job(a, job_id)

    @app.post(f"{p}/jobs/{{job_id}}/cancel")
    def cancel_job(job_id: str, a: Auth) -> JobInfo:
        return be.cancel_job(a, job_id)

    @app.post(f"{p}/worker/heartbeat")
    def worker_heartbeat(req: WorkerHeartbeat, a: Auth) -> WorkerInfo:
        return be.worker_heartbeat(a, req)

    @app.post(
        f"{p}/worker/claim",
        response_model=WorkerJob,
        responses={204: {"description": "no queued job"}},
    )
    def worker_claim(a: Auth) -> Response | WorkerJob:
        job = be.worker_claim(a)
        return job if job is not None else Response(status_code=204)

    @app.post(f"{p}/worker/jobs/{{job_id}}/progress")
    def worker_progress(job_id: str, req: JobProgress, a: Auth) -> JobProgressAck:
        return be.job_progress(a, job_id, req)

    @app.post(f"{p}/worker/jobs/{{job_id}}/finish")
    def worker_finish(job_id: str, req: JobFinish, a: Auth) -> JobInfo:
        return be.job_finish(a, job_id, req)

    # ---------------------------------------------------------------- workspaces & objects
    @app.get(f"{p}/workspaces")
    def list_workspaces(a: Auth) -> list[WorkspaceInfo]:
        return be.list_workspaces(a)

    @app.post(f"{p}/workspaces", status_code=201)
    def create_workspace(req: WorkspaceCreate, a: Auth) -> WorkspaceInfo:
        return be.create_workspace(a, req.name)

    @app.patch(f"{p}/workspaces/{{ws}}")
    def rename_workspace(ws: str, req: WorkspaceCreate, a: Auth) -> WorkspaceInfo:
        return be.rename_workspace(a, ws, req.name)

    @app.get(f"{p}/workspaces/{{ws}}/objects")
    def list_objects(
        ws: str,
        a: Auth,
        kind: ObjectKind | None = None,
        tag: str | None = None,
        slug: str | None = None,
    ) -> list[ObjectInfo]:
        return be.list_objects(a, ws, kind, tag, slug)

    @app.post(f"{p}/workspaces/{{ws}}/objects", status_code=201)
    def create_object(ws: str, req: ObjectCreate, a: Auth) -> ObjectInfo:
        return be.create_object(a, ws, req)

    @app.get(f"{p}/objects/{{object_id}}")
    def get_object(object_id: str, a: Auth) -> ObjectInfo:
        return be.get_object(a, object_id)

    @app.patch(f"{p}/objects/{{object_id}}")
    def update_object(object_id: str, req: ObjectUpdate, a: Auth) -> ObjectInfo:
        return be.update_object(a, object_id, req)

    @app.delete(f"{p}/objects/{{object_id}}", status_code=204)
    def delete_object(object_id: str, a: Auth) -> None:
        be.delete_object(a, object_id)

    @app.post(f"{p}/objects/{{object_id}}/restore")
    def restore_object(object_id: str, a: Auth) -> ObjectInfo:
        return be.restore_object(a, object_id)

    @app.post(f"{p}/objects/{{object_id}}/copy", status_code=201)
    def copy_object(object_id: str, req: ObjectCopy, a: Auth) -> ObjectInfo:
        return be.copy_object(a, object_id, req)

    @app.get(f"{p}/trash")
    def trash(a: Auth) -> list[ObjectInfo]:
        return be.list_trash(a)

    @app.get(f"{p}/objects/{{object_id}}/versions")
    def list_versions(object_id: str, a: Auth) -> list[VersionInfo]:
        return be.list_versions(a, object_id)

    @app.post(f"{p}/objects/{{object_id}}/versions", status_code=201)
    def create_version(object_id: str, req: VersionCreate, a: Auth) -> VersionInfo:
        return be.create_version(a, object_id, req)

    @app.get(f"{p}/versions/{{version_id}}")
    def get_version(version_id: str, a: Auth) -> VersionContent:
        return be.get_version(a, version_id)

    @app.get(f"{p}/objects/{{object_id}}/draft")
    def get_draft(object_id: str, a: Auth) -> DraftOut:
        return be.get_draft(a, object_id)

    @app.put(f"{p}/objects/{{object_id}}/draft")
    def put_draft(object_id: str, req: DraftIn, a: Auth) -> DraftOut:
        return be.put_draft(a, object_id, req.content)

    # ---------------------------------------------------------------- blobs & uploads
    @app.head(f"{p}/blobs/{{sha}}")
    def head_blob(sha: str, a: Auth) -> Response:
        size = be.blob_size(a, sha)
        return Response(headers={"Content-Length": str(size), "Accept-Ranges": "bytes"})

    @app.get(f"{p}/blobs/{{sha}}", response_class=StreamingResponse)
    def get_blob(
        sha: str, a: Auth, range_: Annotated[str | None, Header(alias="Range")] = None
    ) -> Response:
        size = be.blob_size(a, sha)
        headers = {"Accept-Ranges": "bytes", "ETag": f'"{sha}"'}
        if range_ is None:
            headers["Content-Length"] = str(size)
            return StreamingResponse(
                be.read_blob(sha, 0, size), media_type="application/octet-stream", headers=headers
            )
        m = _RANGE.match(range_.strip())
        if m is None or m.group(1) == m.group(2) == "":
            raise ApiError(416, "unsupported Range header")
        if m.group(1) == "":  # suffix range: last n bytes
            start, end = max(0, size - int(m.group(2))), size - 1
        else:
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        if start >= size or end < start:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        headers |= {
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
        }
        return StreamingResponse(
            be.read_blob(sha, start, end - start + 1),
            status_code=206,
            media_type="application/octet-stream",
            headers=headers,
        )

    @app.post(f"{p}/uploads", status_code=201)
    def create_upload(req: UploadCreate, a: Auth) -> UploadInfo:
        return be.create_upload(a, req)

    @app.get(f"{p}/uploads/{{upload_id}}")
    def get_upload(upload_id: str, a: Auth) -> UploadInfo:
        return be.get_upload(a, upload_id)

    @app.put(f"{p}/uploads/{{upload_id}}/parts/{{number}}")
    async def put_part(
        upload_id: str,
        number: int,
        request: Request,
        a: Auth,
        part_sha: Annotated[str | None, Header(alias="X-Part-SHA256")] = None,
    ) -> UploadInfo:
        data = await request.body()
        if len(data) > be.settings.max_part_bytes:
            raise ApiError(413, "part too large")
        return await run_in_threadpool(be.put_part, a, upload_id, number, data, part_sha)

    @app.post(f"{p}/uploads/{{upload_id}}/complete")
    def complete_upload(upload_id: str, a: Auth) -> UploadInfo:
        return be.complete_upload(a, upload_id)

    # ---------------------------------------------------------------- admin & ops
    @app.get(f"{p}/audit")
    def audit(
        a: Auth,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
        action: str | None = None,
    ) -> list[AuditInfo]:
        return be.audit(a, limit, offset, action)

    @app.get(f"{p}/status")
    def status(response: Response) -> Status:
        st = be.status()
        if not (st.database and st.blobs):
            response.status_code = 503
        return st

    return app
