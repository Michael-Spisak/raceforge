"""Engine side of the team workspace (spec 0006): wraps the offline-first sync client."""

import asyncio
import base64
import contextlib
import json
import os
import re
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import segno  # pyright: ignore[reportMissingTypeStubs]

from raceforge.api.models import (
    InboxAction,
    ReceiveRequest,
    SaveAssembly,
    SaveFiles,
    SaveQuickstart,
    TrackScoutPairing,
    WorkspaceLogin,
    WorkspaceRegister,
)
from raceforge.backend.models import (
    ApiTokenInfo,
    InviteInfo,
    TotpSetup,
    UserInfo,
    WorkspaceInfo,
)
from raceforge.capture import rftx
from raceforge.capture.inbox import Inbox, InboxPass, Relay
from raceforge.capture.usb import DeviceFiles, pull_usb
from raceforge.construct.quickstart import generate
from raceforge.core.assembly import Assembly
from raceforge.core.io import load_as, to_jsonable
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


async def _open_usb() -> DeviceFiles:  # pragma: no cover - real device
    from raceforge.capture.usb import IosDeviceFiles

    return await IosDeviceFiles.open()


async def _open_ble() -> rftx.Channel:  # pragma: no cover - real device
    from raceforge.capture.ble import BleChannel

    return await BleChannel.connect()


class WorkspaceApi:
    def __init__(
        self,
        catalogue: Catalogue,
        root: Path | None = None,
        factory: ClientFactory = default_factory,
        open_usb: Callable[[], Awaitable[DeviceFiles]] = _open_usb,
        open_ble: Callable[[], Awaitable[rftx.Channel]] = _open_ble,
    ) -> None:
        self.cat = catalogue
        self.factory = factory
        self.ws = Workspace(root or default_root(), factory)
        self.inbox = Inbox(self.ws.root / "trackscout-inbox")
        self.relay = Relay(self.ws, self.inbox)
        self.open_usb = open_usb
        self.open_ble = open_ble

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

    def save_assembly(self, req: SaveAssembly) -> LocalVersion:
        assembly = load_as(Assembly, req.assembly)  # validates before it is versioned
        assembly.validate_against_parts(self.cat.parts_by_hash())
        return self.ws.save("assembly", req.slug, to_jsonable(assembly), req.message, req.name)

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

    def pair_trackscout(self) -> TrackScoutPairing:
        """New `trackscout` token (read + edit only) and the phone's QR code (spec 0007 AC6)."""
        status = self.ws.status()
        if status.server_url is None:
            raise BackendError(409, "log in first")
        host = urlsplit(status.server_url).hostname or ""
        warning = None
        if host == "localhost" or host == "::1" or host.startswith("127."):
            warning = "loopback"  # the phone cannot reach this address (UI explains --lan)
        laptop = socket.gethostname().removesuffix(".local")
        client = self.ws.client()
        # A new QR replaces this laptop's earlier pairing tokens (old QR screenshots stop working).
        for old in client.tokens():
            if (
                old.client == "trackscout"
                and not old.revoked
                and old.name.startswith(f"TrackScout ({laptop}, ")
            ):
                client.revoke_token(old.id)
        token = client.create_token(
            f"TrackScout ({laptop}, {datetime.now(UTC):%Y-%m-%d})", ["read", "edit"], "trackscout"
        )
        assert token.token is not None
        payload = {
            "server": status.server_url,
            "token": token.token,
            "workspace_id": self.ws.workspace_id,
            "laptop_name": laptop,
            "laptop_key": self.ws.laptop_key(),
        }
        data = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        url = f"raceforge://pair?v=1&d={data}"
        svg = segno.make(url, error="m").svg_inline(scale=4, border=2)
        return TrackScoutPairing(
            url=url,
            qr_svg=svg,
            token_id=token.id,
            laptop_name=laptop,
            workspace_id=self.ws.workspace_id,
            warning=warning,
        )

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

    # ------------------------------------------------- TrackScout inbox (spec 0007 scope 9)
    def trackscout_inbox(self) -> list[InboxPass]:
        return self.inbox.list()

    def trackscout_choose(self, pass_id: str, req: InboxAction) -> InboxPass:
        return self.relay.choose(pass_id, req.action)

    async def trackscout_receive(self, req: ReceiveRequest) -> list[InboxPass]:
        """Pull every pass the paired phone offers, then relay each one (or let it wait)."""
        key = self.ws.laptop_key()
        laptop = socket.gethostname().removesuffix(".local")
        if req.source == "usb":
            device = await self.open_usb()
            try:
                done = await pull_usb(device, key, laptop, self.inbox)
            finally:
                close = getattr(device, "aclose", None)
                if close is not None:
                    await close()
        else:
            channel = await self.open_ble()
            try:
                done = await rftx.pull(channel, key, laptop, self.inbox)
            finally:
                close = getattr(channel, "close", None)
                if close is not None:
                    await close()
        for pass_id in done:
            await asyncio.to_thread(self.relay.upload, pass_id)
        return [p for p in self.inbox.list() if p.id in done]
