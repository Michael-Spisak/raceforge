"""HTTP client for the team backend (spec 0006 API) with token refresh and resumable transfers."""

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from raceforge.backend.models import (
    ApiTokenInfo,
    InviteInfo,
    JobCreate,
    JobInfo,
    ObjectInfo,
    Status,
    TokenPair,
    TotpSetup,
    UploadInfo,
    UserInfo,
    VersionContent,
    VersionInfo,
    WorkerInfo,
    WorkerJob,
    WorkerRegistration,
    WorkspaceInfo,
)

ClientFactory = Callable[[str], httpx.Client]


class OfflineError(Exception):
    """The backend cannot be reached."""


class BackendError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


def default_factory(base_url: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=httpx.Timeout(30.0, connect=3.0))


class BackendClient:
    def __init__(
        self,
        base_url: str,
        access: str | None = None,
        refresh: str | None = None,
        on_tokens: Callable[[TokenPair], None] | None = None,
        factory: ClientFactory = default_factory,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = factory(self.base_url)
        self.access, self.refresh_token = access, refresh
        self.on_tokens = on_tokens

    def close(self) -> None:
        self.http.close()

    # ------------------------------------------------------------ plumbing
    def _send(self, method: str, path: str, **kw: Any) -> httpx.Response:
        headers: dict[str, str] = dict(kw.pop("headers", {}))
        if self.access:
            headers["Authorization"] = f"Bearer {self.access}"
        try:
            return self.http.request(method, f"/api/v1{path}", headers=headers, **kw)
        except httpx.TransportError as exc:
            raise OfflineError(str(exc)) from exc

    def request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        r = self._send(method, path, **kw)
        if r.status_code == 401 and self.refresh_token and not path.startswith("/auth/"):
            self._refresh()
            r = self._send(method, path, **kw)
        if r.status_code >= 400:
            try:
                detail = str(r.json().get("detail", r.text))
            except ValueError:
                detail = r.text
            raise BackendError(r.status_code, detail)
        return r

    def _refresh(self) -> None:
        r = self._send("POST", "/auth/refresh", json={"refresh_token": self.refresh_token})
        if r.status_code != 200:
            self.access = self.refresh_token = None
            return
        self._use(TokenPair.model_validate(r.json()))

    def _use(self, pair: TokenPair) -> None:
        self.access, self.refresh_token = pair.access_token, pair.refresh_token
        self.http.cookies.clear()  # bearer only; never mix with cookie sessions
        if self.on_tokens:
            self.on_tokens(pair)

    # ------------------------------------------------------------ auth
    def status(self) -> Status:
        return Status.model_validate(self.request("GET", "/status").json())

    def login(self, username: str, password: str, totp: str | None) -> TokenPair:
        body = {"username": username, "password": password, "totp": totp, "client": "desktop"}
        pair = TokenPair.model_validate(self.request("POST", "/auth/login", json=body).json())
        self._use(pair)
        return pair

    def logout(self) -> None:
        try:
            self.request("POST", "/auth/logout")
        finally:
            self.access = self.refresh_token = None

    def register(self, invite: str, username: str, display_name: str, password: str) -> UserInfo:
        body = {
            "invite_token": invite,
            "username": username,
            "display_name": display_name,
            "password": password,
        }
        return UserInfo.model_validate(self.request("POST", "/auth/register", json=body).json())

    def totp_setup(self) -> TotpSetup:
        return TotpSetup.model_validate(self.request("POST", "/auth/totp/setup").json())

    def totp_verify(self, code: str) -> UserInfo:
        r = self.request("POST", "/auth/totp/verify", json={"code": code})
        return UserInfo.model_validate(r.json())

    def me(self) -> UserInfo:
        return UserInfo.model_validate(self.request("GET", "/users/me").json())

    # ------------------------------------------------------------ admin
    def invites(self) -> list[InviteInfo]:
        return [InviteInfo.model_validate(x) for x in self.request("GET", "/invites").json()]

    def create_invite(self, role: str) -> InviteInfo:
        r = self.request("POST", "/invites", json={"role": role})
        return InviteInfo.model_validate(r.json())

    def tokens(self) -> list[ApiTokenInfo]:
        return [ApiTokenInfo.model_validate(x) for x in self.request("GET", "/tokens").json()]

    def create_token(self, name: str, scopes: list[str], client: str) -> ApiTokenInfo:
        body = {"name": name, "scopes": scopes, "client": client}
        return ApiTokenInfo.model_validate(self.request("POST", "/tokens", json=body).json())

    def revoke_token(self, token_id: str) -> None:
        self.request("DELETE", f"/tokens/{token_id}")

    # ------------------------------------------------------------ workers & jobs (spec 0020)
    def register_worker(self, workspace_id: str, name: str) -> WorkerRegistration:
        body = {"workspace_id": workspace_id, "name": name}
        return WorkerRegistration.model_validate(self.request("POST", "/workers", json=body).json())

    def workers(self, workspace_id: str) -> list[WorkerInfo]:
        r = self.request("GET", f"/workspaces/{workspace_id}/workers")
        return [WorkerInfo.model_validate(x) for x in r.json()]

    def remove_worker(self, worker_id: str) -> None:
        self.request("DELETE", f"/workers/{worker_id}")

    def create_job(self, workspace_id: str, job: JobCreate) -> JobInfo:
        r = self.request(
            "POST", f"/workspaces/{workspace_id}/jobs", json=job.model_dump(mode="json")
        )
        return JobInfo.model_validate(r.json())

    def jobs(self, workspace_id: str, limit: int = 50) -> list[JobInfo]:
        r = self.request("GET", f"/workspaces/{workspace_id}/jobs", params={"limit": limit})
        return [JobInfo.model_validate(x) for x in r.json()]

    def job(self, job_id: str) -> JobInfo:
        return JobInfo.model_validate(self.request("GET", f"/jobs/{job_id}").json())

    def cancel_job(self, job_id: str) -> JobInfo:
        return JobInfo.model_validate(self.request("POST", f"/jobs/{job_id}/cancel").json())

    # worker-token calls
    def worker_heartbeat(self, info: dict[str, Any]) -> WorkerInfo:
        r = self.request("POST", "/worker/heartbeat", json={"info": info})
        return WorkerInfo.model_validate(r.json())

    def worker_claim(self) -> WorkerJob | None:
        r = self.request("POST", "/worker/claim")
        return None if r.status_code == 204 else WorkerJob.model_validate(r.json())

    def job_progress(self, job_id: str, progress: dict[str, Any], log: list[str]) -> bool:
        """Report progress; returns True when the user asked to cancel."""
        body = {"progress": progress, "log": log}
        r = self.request("POST", f"/worker/jobs/{job_id}/progress", json=body)
        return bool(r.json()["cancel"])

    def job_finish(
        self, job_id: str, status: str, result: dict[str, Any] | None = None, error: str = ""
    ) -> JobInfo:
        body = {"status": status, "result": result, "error": error}
        r = self.request("POST", f"/worker/jobs/{job_id}/finish", json=body)
        return JobInfo.model_validate(r.json())

    # ------------------------------------------------------------ data
    def workspaces(self) -> list[WorkspaceInfo]:
        return [WorkspaceInfo.model_validate(x) for x in self.request("GET", "/workspaces").json()]

    def create_workspace(self, name: str) -> WorkspaceInfo:
        r = self.request("POST", "/workspaces", json={"name": name})
        return WorkspaceInfo.model_validate(r.json())

    def objects(self, ws: str) -> list[ObjectInfo]:
        r = self.request("GET", f"/workspaces/{ws}/objects")
        return [ObjectInfo.model_validate(x) for x in r.json()]

    def create_object(self, ws: str, object_id: str, kind: str, slug: str) -> ObjectInfo:
        body = {"id": object_id, "kind": kind, "slug": slug}
        r = self.request("POST", f"/workspaces/{ws}/objects", json=body)
        return ObjectInfo.model_validate(r.json())

    def versions(self, object_id: str) -> list[VersionInfo]:
        r = self.request("GET", f"/objects/{object_id}/versions")
        return [VersionInfo.model_validate(x) for x in r.json()]

    def create_version(
        self,
        object_id: str,
        version_id: str,
        content: dict[str, Any],
        parents: list[str],
        message: str,
        name: str | None,
    ) -> VersionInfo:
        body = {
            "id": version_id,
            "content": content,
            "parents": parents,
            "message": message,
            "name": name,
        }
        r = self.request("POST", f"/objects/{object_id}/versions", json=body)
        return VersionInfo.model_validate(r.json())

    def version(self, version_id: str) -> VersionContent:
        return VersionContent.model_validate(self.request("GET", f"/versions/{version_id}").json())

    # ------------------------------------------------------------ blobs
    def download_blob(self, sha: str, dest: Path) -> None:
        """Download into ``dest`` (resuming a previous ``.part`` file) and verify the checksum."""
        part = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        if self.access:
            headers["Authorization"] = f"Bearer {self.access}"
        try:
            with self.http.stream("GET", f"/api/v1/blobs/{sha}", headers=headers) as r:
                if r.status_code == 416:
                    pass  # .part is already complete
                elif r.status_code not in (200, 206):
                    r.read()
                    raise BackendError(r.status_code, r.text)
                else:
                    with part.open("ab" if r.status_code == 206 else "wb") as f:
                        for chunk in r.iter_bytes():
                            f.write(chunk)
        except httpx.TransportError as exc:
            raise OfflineError(str(exc)) from exc
        digest = hashlib.sha256()
        with part.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != sha:
            part.unlink()
            raise BackendError(500, f"downloaded blob {sha} is corrupt")
        part.replace(dest)

    def upload_blob(
        self, path: Path, sha: str, progress: Callable[[int, int, int], None] | None = None
    ) -> None:
        """Chunked, resumable upload; parts the server already has are skipped.

        ``progress(done, total, sent)`` runs after every part (``sent``: bytes sent by this call);
        it may raise to stop the upload — finished parts stay on the server for a later resume.
        """
        size = path.stat().st_size
        info = UploadInfo.model_validate(
            self.request("POST", "/uploads", json={"sha256": sha, "size": size}).json()
        )
        if info.status == "exists":
            return
        assert info.id is not None
        done = set(info.received)
        have = sum(min(info.part_size, size - n * info.part_size) for n in done)
        sent = 0
        with path.open("rb") as f:
            for n in range(info.parts):
                if n in done:
                    continue
                f.seek(n * info.part_size)
                data = f.read(info.part_size)
                self.request(
                    "PUT",
                    f"/uploads/{info.id}/parts/{n}",
                    content=data,
                    headers={"X-Part-SHA256": hashlib.sha256(data).hexdigest()},
                )
                sent += len(data)
                if progress:
                    progress(have + sent, size, sent)
        self.request("POST", f"/uploads/{info.id}/complete")
