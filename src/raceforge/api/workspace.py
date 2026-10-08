"""Engine side of the team workspace (spec 0006): wraps the offline-first sync client."""

import contextlib
import os
import re
from pathlib import Path
from typing import Any

from raceforge.api.models import SaveFiles, SaveQuickstart, WorkspaceLogin, WorkspaceRegister
from raceforge.backend.models import (
    ApiTokenInfo,
    InviteInfo,
    TotpSetup,
    UserInfo,
    WorkspaceInfo,
)
from raceforge.construct.quickstart import generate
from raceforge.core.io import to_jsonable
from raceforge.parts.catalogue import Catalogue
from raceforge.workspace.client import (
    BackendClient,
    BackendError,
    ClientFactory,
    OfflineError,
    default_factory,
)
from raceforge.workspace.sync import (
    Conflict,
    LocalObject,
    LocalVersion,
    SyncResult,
    Workspace,
    WorkspaceStatus,
)


def default_root() -> Path:
    env = os.environ.get("RACEFORGE_WORKSPACE_DIR")
    return Path(env) if env else Path.home() / ".cache" / "raceforge" / "workspace"


def invite_token(invite: str) -> str:
    """Accept a full invite link (``…/invite#rfi_…``) or the bare token."""
    m = re.search(r"(rfi_[A-Za-z0-9_-]+)", invite)
    return m.group(1) if m else invite.strip()


class WorkspaceApi:
    def __init__(
        self,
        catalogue: Catalogue,
        root: Path | None = None,
        factory: ClientFactory = default_factory,
    ) -> None:
        self.cat = catalogue
        self.factory = factory
        self.ws = Workspace(root or default_root(), factory)

    def status(self, probe: bool) -> WorkspaceStatus:
        return self.ws.status(probe)

    def login(self, req: WorkspaceLogin) -> WorkspaceStatus:
        self.ws.login(req.server_url, req.username, req.password, req.totp)
        workspaces = self.ws.workspaces()
        if self.ws.workspace_id is None and len(workspaces) == 1:
            self.ws.select(workspaces[0].id)
        return self.ws.status()

    def register(self, req: WorkspaceRegister) -> UserInfo:
        client = BackendClient(req.server_url, factory=self.factory)
        try:
            return client.register(
                invite_token(req.invite), req.username, req.display_name, req.password
            )
        finally:
            client.close()

    def logout(self) -> WorkspaceStatus:
        self.ws.logout()
        return self.ws.status()

    def workspaces(self) -> list[WorkspaceInfo]:
        return self.ws.workspaces()

    def create_workspace(self, name: str) -> WorkspaceInfo:
        return self.ws.create_workspace(name)

    def select(self, workspace_id: str) -> WorkspaceStatus:
        self.ws.select(workspace_id)
        with contextlib.suppress(OfflineError, BackendError):  # offline: show the cached copy
            self.ws.sync()
        return self.ws.status()

    def sync(self) -> SyncResult:
        return self.ws.sync()

    def objects(self, kind: str | None) -> list[LocalObject]:
        return self.ws.objects(kind)

    def history(self, object_id: str) -> list[LocalVersion]:
        return self.ws.history(object_id)

    def version(self, version_id: str) -> dict[str, Any]:
        return self.ws.version_content(version_id)

    def conflicts(self) -> list[Conflict]:
        return self.ws.conflicts()

    def save_quickstart(self, req: SaveQuickstart) -> LocalVersion:
        assembly = to_jsonable(generate(req.params, self.cat).assembly)
        return self.ws.save("assembly", req.slug, assembly, req.message, req.name)

    def save_files(self, req: SaveFiles) -> LocalVersion:
        paths = [Path(p).expanduser() for p in req.paths]
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            raise FileNotFoundError(", ".join(missing))
        return self.ws.save_files(req.kind, req.slug, paths, req.message)

    def totp_setup(self) -> TotpSetup:
        return self.ws.client().totp_setup()

    def totp_verify(self, code: str) -> UserInfo:
        return self.ws.totp_verify(code)

    def invites(self) -> list[InviteInfo]:
        return self.ws.client().invites()

    def create_invite(self, role: str) -> InviteInfo:
        return self.ws.client().create_invite(role)

    def tokens(self) -> list[ApiTokenInfo]:
        return self.ws.client().tokens()

    def create_token(self, name: str, scopes: list[str], client: str) -> ApiTokenInfo:
        return self.ws.client().create_token(name, scopes, client)

    def revoke_token(self, token_id: str) -> None:
        self.ws.client().revoke_token(token_id)
