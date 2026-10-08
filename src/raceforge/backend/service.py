"""Backend logic: auth, workspaces, objects/versions, drafts, blobs, trash, audit (spec 0006).

Every public method opens its own database transaction; every write is recorded in the audit log.
"""

import hashlib
import math
import shutil
import tempfile
import threading
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, defer

from raceforge import __version__
from raceforge.backend import db
from raceforge.backend.blobs import BlobStore, blob_key, part_key
from raceforge.backend.models import (
    ApiTokenCreate,
    ApiTokenInfo,
    AuditInfo,
    DraftOut,
    FileSetContent,
    InviteCreate,
    InviteInfo,
    LoginRequest,
    ObjectCopy,
    ObjectCreate,
    ObjectInfo,
    ObjectUpdate,
    RegisterRequest,
    Role,
    Scope,
    Status,
    TokenPair,
    TotpSetup,
    UploadCreate,
    UploadInfo,
    UserInfo,
    VersionContent,
    VersionCreate,
    VersionInfo,
    WorkspaceInfo,
)
from raceforge.backend.security import (
    Passwords,
    new_secret,
    new_totp_secret,
    token_hash,
    totp_ok,
    totp_uri,
)
from raceforge.backend.settings import Settings
from raceforge.core import io
from raceforge.core.ids import new_object_id
from raceforge.core.meta import ObjectKind

ROLE_SCOPES: dict[str, frozenset[str]] = {
    Role.MEMBER: frozenset({Scope.READ, Scope.SIM_TRAIN, Scope.EDIT}),
    Role.ADMIN: frozenset({Scope.READ, Scope.SIM_TRAIN, Scope.EDIT, Scope.ADMIN}),
}
# Kinds whose versions are core documents (spec 0001); other kinds store a FileSetContent.
KIND_SCHEMA: dict[str, str] = {
    ObjectKind.PART: "part",
    ObjectKind.ASSEMBLY: "assembly",
    ObjectKind.TRACK: "track",
    ObjectKind.RUN: "runlog",
}


class ApiError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Actor:
    user_id: str
    username: str
    role: str
    token_id: str
    token_kind: str  # access | api
    session_id: str | None
    scopes: frozenset[str]
    client: str
    totp_enabled: bool
    totp_verified: bool


def _user_info(u: db.User) -> UserInfo:
    return UserInfo(
        id=u.id,
        username=u.username,
        display_name=u.display_name,
        role=Role(u.role),
        totp_enabled=u.totp_enabled,
    )


def _version_info(v: db.Version, authors: dict[str, str]) -> VersionInfo:
    return VersionInfo(
        id=v.id,
        object_id=v.object_id,
        semver=v.semver,
        name=v.name,
        message=v.message,
        author=authors.get(v.author, v.author),
        created_at=v.created_at,
        content_hash=v.content_hash,
        parents=list(v.parents),
        branch=v.branch,
    )


def _bump_patch(semver: str) -> str:
    major, minor, patch = (int(p) for p in semver.split("."))
    return f"{major}.{minor}.{patch + 1}"


class Backend:
    def __init__(
        self,
        settings: Settings,
        database: db.Database,
        store: BlobStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.db = database
        self.store = store
        self.clock = clock
        self.passwords = Passwords(settings.fast_password_hash)
        self._failures: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ helpers
    def _audit(
        self,
        s: Session,
        actor: Actor | None,
        action: str,
        target: str = "",
        **detail: Any,
    ) -> None:
        s.add(
            db.AuditEntry(
                at=self.clock(),
                user_id=actor.user_id if actor else None,
                token_id=actor.token_id if actor else None,
                client=actor.client if actor else "system",
                action=action,
                target=target,
                detail=detail,
            )
        )

    def _usernames(self, s: Session, ids: set[str]) -> dict[str, str]:
        if not ids:
            return {}
        rows = s.execute(select(db.User.id, db.User.username).where(db.User.id.in_(ids)))
        return {r.id: r.username for r in rows}

    def _object(self, s: Session, object_id: str, actor: Actor | None = None) -> db.Object:
        obj = s.get(db.Object, object_id)
        if obj is None or (
            obj.deleted_at is not None and (actor is None or Scope.ADMIN not in actor.scopes)
        ):
            raise ApiError(404, f"object {object_id} not found")
        return obj

    def _latest(self, s: Session, object_id: str) -> db.Version | None:
        return s.scalars(
            select(db.Version)
            .options(defer(db.Version.content))
            .where(db.Version.object_id == object_id)
            .order_by(db.Version.created_at.desc(), db.Version.id.desc())
            .limit(1)
        ).first()

    def require(self, actor: Actor, scope: Scope) -> None:
        if scope not in actor.scopes:
            raise ApiError(403, f"token lacks the '{scope}' scope")
        if scope == Scope.ADMIN and not (actor.totp_enabled and actor.totp_verified):
            raise ApiError(403, "admin actions require TOTP 2FA: set it up under auth/totp")

    # ------------------------------------------------------------------ auth
    def bootstrap_admin(self, username: str, password: str, display_name: str = "") -> UserInfo:
        with self.db.session() as s:
            if s.scalars(select(db.User).where(db.User.username == username)).first():
                raise ApiError(409, f"user {username!r} exists")
            user = db.User(
                id=new_object_id(),
                username=username,
                display_name=display_name or username,
                password_hash=self.passwords.hash(password),
                role=Role.ADMIN,
                totp_enabled=False,
                disabled=False,
                created_at=self.clock(),
            )
            s.add(user)
            self._audit(s, None, "user.bootstrap_admin", user.id, username=username)
            return _user_info(user)

    def _rate_limited(self, keys: list[str]) -> bool:
        now = self.clock()
        window = timedelta(seconds=self.settings.login_window_s)
        with self._lock:
            for key in keys:
                q = self._failures.get(key)
                while q and now - q[0] > window:
                    q.popleft()
                if q and len(q) >= self.settings.login_max_failures:
                    return True
        return False

    def _fail(self, keys: list[str]) -> None:
        with self._lock:
            for key in keys:
                self._failures.setdefault(key, deque()).append(self.clock())

    def _issue_session(
        self, s: Session, user: db.User, client: str, totp_verified: bool, session_id: str
    ) -> TokenPair:
        now = self.clock()
        access, refresh = new_secret("rfa"), new_secret("rfr")
        scopes = sorted(ROLE_SCOPES[user.role])
        for kind, secret, ttl in (
            ("access", access, self.settings.access_ttl_s),
            ("refresh", refresh, self.settings.refresh_ttl_s),
        ):
            s.add(
                db.Token(
                    id=new_object_id(),
                    user_id=user.id,
                    kind=kind,
                    token_hash=token_hash(secret),
                    name=f"{client} session",
                    scopes=scopes,
                    client=client,
                    totp_verified=totp_verified,
                    session_id=session_id,
                    created_at=now,
                    expires_at=now + timedelta(seconds=ttl),
                )
            )
        return TokenPair(
            access_token=access,
            refresh_token=refresh,
            expires_in=self.settings.access_ttl_s,
            user=_user_info(user),
            totp_verified=totp_verified,
        )

    def login(self, req: LoginRequest, ip: str) -> TokenPair:
        keys = [f"user:{req.username.lower()}", f"ip:{ip}"]
        if self._rate_limited(keys):
            raise ApiError(429, "too many failed logins, try again later")
        with self.db.session() as s:
            user = s.scalars(select(db.User).where(db.User.username == req.username)).first()
            if (
                user is None
                or user.disabled
                or not self.passwords.verify(user.password_hash, req.password)
            ):
                self._fail(keys)
                raise ApiError(401, "wrong username or password")
            if user.totp_enabled:
                if not req.totp:
                    raise ApiError(401, "totp_required")
                if not totp_ok(user.totp_secret or "", req.totp, self.clock()):
                    self._fail(keys)
                    raise ApiError(401, "wrong TOTP code")
            pair = self._issue_session(s, user, req.client, user.totp_enabled, new_object_id())
            self._audit(
                s,
                None,
                "auth.login",
                user.id,
                username=user.username,
                client=req.client,
                ip=ip,
            )
            return pair

    def refresh(self, secret: str) -> TokenPair:
        now = self.clock()
        with self.db.session() as s:
            tok = s.scalars(
                select(db.Token).where(db.Token.token_hash == token_hash(secret))
            ).first()
            if tok is None or tok.kind != "refresh":
                raise ApiError(401, "invalid refresh token")
            if tok.revoked_at is not None:  # reuse of a rotated token: revoke the whole session
                self._revoke_session(s, tok.session_id, now)
                self._audit(s, None, "auth.refresh_reuse", tok.user_id, session=tok.session_id)
                reused = True
            else:
                reused = False
        if reused:  # raised after the commit so the revocation sticks
            raise ApiError(401, "refresh token was already used; session revoked")
        with self.db.session() as s:
            tok = s.scalars(
                select(db.Token).where(db.Token.token_hash == token_hash(secret))
            ).first()
            assert tok is not None
            if tok.expires_at is not None and tok.expires_at <= now:
                raise ApiError(401, "refresh token expired")
            user = s.get(db.User, tok.user_id)
            if user is None or user.disabled:
                raise ApiError(401, "user disabled")
            self._revoke_session(s, tok.session_id, now)
            return self._issue_session(
                s, user, tok.client, tok.totp_verified, tok.session_id or new_object_id()
            )

    def _revoke_session(self, s: Session, session_id: str | None, now: datetime) -> None:
        if session_id is None:
            return
        for t in s.scalars(
            select(db.Token).where(db.Token.session_id == session_id, db.Token.revoked_at.is_(None))
        ):
            t.revoked_at = now

    def logout(self, actor: Actor) -> None:
        with self.db.session() as s:
            if actor.session_id:
                self._revoke_session(s, actor.session_id, self.clock())
            self._audit(s, actor, "auth.logout", actor.user_id)

    def authenticate(self, secret: str) -> Actor:
        now = self.clock()
        with self.db.session() as s:
            tok = s.scalars(
                select(db.Token).where(db.Token.token_hash == token_hash(secret))
            ).first()
            if (
                tok is None
                or tok.kind not in ("access", "api")
                or tok.revoked_at is not None
                or (tok.expires_at is not None and tok.expires_at <= now)
            ):
                raise ApiError(401, "invalid or expired token")
            user = s.get(db.User, tok.user_id)
            if user is None or user.disabled:
                raise ApiError(401, "user disabled")
            if tok.last_used_at is None or now - tok.last_used_at > timedelta(minutes=1):
                tok.last_used_at = now
            # A token never has more rights than its user's current role.
            scopes = frozenset(tok.scopes) & ROLE_SCOPES[user.role]
            return Actor(
                user_id=user.id,
                username=user.username,
                role=user.role,
                token_id=tok.id,
                token_kind=tok.kind,
                session_id=tok.session_id,
                scopes=scopes,
                client=tok.client,
                totp_enabled=user.totp_enabled,
                totp_verified=tok.totp_verified,
            )

    def me(self, actor: Actor) -> UserInfo:
        with self.db.session() as s:
            user = s.get(db.User, actor.user_id)
            assert user is not None
            return _user_info(user)

    def totp_setup(self, actor: Actor) -> TotpSetup:
        with self.db.session() as s:
            user = s.get(db.User, actor.user_id)
            assert user is not None
            if user.totp_enabled:
                raise ApiError(409, "TOTP is already enabled")
            secret = user.totp_secret = new_totp_secret()
            self._audit(s, actor, "auth.totp_setup", user.id)
            return TotpSetup(secret=secret, uri=totp_uri(secret, user.username))

    def totp_verify(self, actor: Actor, code: str) -> UserInfo:
        with self.db.session() as s:
            user = s.get(db.User, actor.user_id)
            assert user is not None
            if not user.totp_secret or not totp_ok(user.totp_secret, code, self.clock()):
                raise ApiError(400, "wrong TOTP code")
            user.totp_enabled = True
            if actor.session_id:  # the current login has now passed 2FA
                for t in s.scalars(select(db.Token).where(db.Token.session_id == actor.session_id)):
                    t.totp_verified = True
            self._audit(s, actor, "auth.totp_enabled", user.id)
            return _user_info(user)

    # ------------------------------------------------------------------ invites & users
    def create_invite(self, actor: Actor, req: InviteCreate) -> InviteInfo:
        self.require(actor, Scope.ADMIN)
        secret = new_secret("rfi")
        now = self.clock()
        with self.db.session() as s:
            inv = db.Invite(
                id=new_object_id(),
                token_hash=token_hash(secret),
                role=req.role,
                email=req.email,
                created_by=actor.user_id,
                created_at=now,
                expires_at=now + timedelta(seconds=self.settings.invite_ttl_s),
            )
            s.add(inv)
            self._audit(s, actor, "invite.create", inv.id, role=req.role, email=req.email)
            info = self._invite_info(inv)
        base = self.settings.public_url.rstrip("/")
        return info.model_copy(update={"token": secret, "link": f"{base}/invite#{secret}"})

    @staticmethod
    def _invite_info(inv: db.Invite) -> InviteInfo:
        return InviteInfo(
            id=inv.id,
            role=Role(inv.role),
            email=inv.email,
            created_at=inv.created_at,
            expires_at=inv.expires_at,
            used_at=inv.used_at,
        )

    def list_invites(self, actor: Actor) -> list[InviteInfo]:
        self.require(actor, Scope.ADMIN)
        with self.db.session() as s:
            invs = s.scalars(select(db.Invite).order_by(db.Invite.created_at.desc()))
            return [self._invite_info(i) for i in invs]

    def register(self, req: RegisterRequest) -> UserInfo:
        now = self.clock()
        with self.db.session() as s:
            inv = s.scalars(
                select(db.Invite).where(db.Invite.token_hash == token_hash(req.invite_token))
            ).first()
            if inv is None or inv.used_at is not None or inv.expires_at <= now:
                raise ApiError(400, "invite link is invalid, used or expired")
            if s.scalars(select(db.User).where(db.User.username == req.username)).first():
                raise ApiError(409, f"username {req.username!r} is taken")
            user = db.User(
                id=new_object_id(),
                username=req.username,
                display_name=req.display_name,
                password_hash=self.passwords.hash(req.password),
                role=inv.role,
                totp_enabled=False,
                disabled=False,
                created_at=now,
            )
            s.add(user)
            s.flush()
            inv.used_at, inv.used_by = now, user.id
            self._audit(s, None, "user.register", user.id, username=user.username, invite=inv.id)
            return _user_info(user)

    # ------------------------------------------------------------------ API tokens
    def _token_info(self, t: db.Token, users: dict[str, str]) -> ApiTokenInfo:
        return ApiTokenInfo(
            id=t.id,
            name=t.name,
            scopes=[Scope(x) for x in t.scopes],
            client=t.client,
            created_at=t.created_at,
            expires_at=t.expires_at,
            last_used_at=t.last_used_at,
            revoked=t.revoked_at is not None,
            user=users.get(t.user_id, t.user_id),
        )

    def create_token(self, actor: Actor, req: ApiTokenCreate) -> ApiTokenInfo:
        if actor.token_kind != "access":
            raise ApiError(403, "API tokens can only be created from a login session")
        scopes = set(req.scopes)
        if Scope.ADMIN in scopes:
            if req.client == "mcp":
                raise ApiError(422, "MCP tokens can never have the admin scope")
            self.require(actor, Scope.ADMIN)
        if not scopes <= actor.scopes:
            raise ApiError(403, "a token cannot have more scopes than its owner")
        if req.expires_at is not None and req.expires_at <= self.clock():
            raise ApiError(422, "expires_at is in the past")
        secret = new_secret("rft")
        with self.db.session() as s:
            tok = db.Token(
                id=new_object_id(),
                user_id=actor.user_id,
                kind="api",
                token_hash=token_hash(secret),
                name=req.name,
                scopes=sorted(scopes),
                client=req.client,
                totp_verified=actor.totp_verified,
                created_at=self.clock(),
                expires_at=req.expires_at,
            )
            s.add(tok)
            self._audit(s, actor, "token.create", tok.id, scopes=sorted(scopes), client=req.client)
            info = self._token_info(tok, {actor.user_id: actor.username})
        return info.model_copy(update={"token": secret})

    def list_tokens(self, actor: Actor) -> list[ApiTokenInfo]:
        with self.db.session() as s:
            q = select(db.Token).where(db.Token.kind == "api").order_by(db.Token.created_at)
            if Scope.ADMIN not in actor.scopes or not actor.totp_verified:
                q = q.where(db.Token.user_id == actor.user_id)
            toks = list(s.scalars(q))
            users = self._usernames(s, {t.user_id for t in toks})
            return [self._token_info(t, users) for t in toks]

    def revoke_token(self, actor: Actor, token_id: str) -> None:
        with self.db.session() as s:
            tok = s.get(db.Token, token_id)
            if tok is None or tok.kind != "api":
                raise ApiError(404, "token not found")
            if tok.user_id != actor.user_id:
                self.require(actor, Scope.ADMIN)
            tok.revoked_at = tok.revoked_at or self.clock()
            self._audit(s, actor, "token.revoke", tok.id)

    # ------------------------------------------------------------------ workspaces
    def list_workspaces(self, actor: Actor) -> list[WorkspaceInfo]:
        self.require(actor, Scope.READ)
        with self.db.session() as s:
            rows = s.scalars(select(db.Workspace).order_by(db.Workspace.name))
            return [WorkspaceInfo(id=w.id, name=w.name, created_at=w.created_at) for w in rows]

    def create_workspace(self, actor: Actor, name: str) -> WorkspaceInfo:
        self.require(actor, Scope.EDIT)
        with self.db.session() as s:
            if s.scalars(select(db.Workspace).where(db.Workspace.name == name)).first():
                raise ApiError(409, f"workspace {name!r} exists")
            ws = db.Workspace(
                id=new_object_id(), name=name, created_by=actor.user_id, created_at=self.clock()
            )
            s.add(ws)
            self._audit(s, actor, "workspace.create", ws.id, name=name)
            return WorkspaceInfo(id=ws.id, name=ws.name, created_at=ws.created_at)

    def rename_workspace(self, actor: Actor, ws_id: str, name: str) -> WorkspaceInfo:
        self.require(actor, Scope.EDIT)
        with self.db.session() as s:
            ws = s.get(db.Workspace, ws_id)
            if ws is None:
                raise ApiError(404, "workspace not found")
            if s.scalars(
                select(db.Workspace).where(db.Workspace.name == name, db.Workspace.id != ws_id)
            ).first():
                raise ApiError(409, f"workspace {name!r} exists")
            old, ws.name = ws.name, name
            self._audit(s, actor, "workspace.rename", ws.id, old=old, new=name)
            return WorkspaceInfo(id=ws.id, name=ws.name, created_at=ws.created_at)

    # ------------------------------------------------------------------ objects
    def _object_infos(self, s: Session, objs: list[db.Object]) -> list[ObjectInfo]:
        ids = [o.id for o in objs]
        latest: dict[str, db.Version] = {}
        if ids:
            ranked = (
                select(
                    db.Version.id,
                    func.row_number()
                    .over(
                        partition_by=db.Version.object_id,
                        order_by=(db.Version.created_at.desc(), db.Version.id.desc()),
                    )
                    .label("rn"),
                )
                .where(db.Version.object_id.in_(ids))
                .subquery()
            )
            rows = s.scalars(
                select(db.Version)
                .options(defer(db.Version.content))
                .join(ranked, ranked.c.id == db.Version.id)
                .where(ranked.c.rn == 1)
            )
            latest = {v.object_id: v for v in rows}
        users = self._usernames(
            s, {o.created_by for o in objs} | {v.author for v in latest.values()}
        )
        return [
            ObjectInfo(
                id=o.id,
                workspace_id=o.workspace_id,
                kind=ObjectKind(o.kind),
                slug=o.slug,
                tags=list(o.tags),
                created_by=users.get(o.created_by, o.created_by),
                created_at=o.created_at,
                copied_from=o.copied_from,
                deleted_at=o.deleted_at,
                latest=_version_info(latest[o.id], users) if o.id in latest else None,
            )
            for o in objs
        ]

    def list_objects(
        self,
        actor: Actor,
        ws_id: str,
        kind: ObjectKind | None = None,
        tag: str | None = None,
        slug: str | None = None,
    ) -> list[ObjectInfo]:
        self.require(actor, Scope.READ)
        with self.db.session() as s:
            if s.get(db.Workspace, ws_id) is None:
                raise ApiError(404, "workspace not found")
            q = select(db.Object).where(
                db.Object.workspace_id == ws_id, db.Object.deleted_at.is_(None)
            )
            if kind is not None:
                q = q.where(db.Object.kind == kind)
            if slug:
                q = q.where(db.Object.slug == slug)
            objs = list(s.scalars(q.order_by(db.Object.slug)))
            if tag:
                objs = [o for o in objs if tag in o.tags]
            return self._object_infos(s, objs)

    def create_object(self, actor: Actor, ws_id: str, req: ObjectCreate) -> ObjectInfo:
        self.require(actor, Scope.EDIT)
        with self.db.session() as s:
            if s.get(db.Workspace, ws_id) is None:
                raise ApiError(404, "workspace not found")
            if req.id and (existing := s.get(db.Object, req.id)) is not None:
                if (existing.workspace_id, existing.kind, existing.slug) != (
                    ws_id,
                    req.kind,
                    req.slug,
                ):
                    raise ApiError(409, f"object id {req.id} is used by another object")
                return self._object_infos(s, [existing])[0]
            if s.scalars(
                select(db.Object).where(db.Object.workspace_id == ws_id, db.Object.slug == req.slug)
            ).first():
                raise ApiError(409, f"slug {req.slug!r} already exists in this workspace")
            obj = db.Object(
                id=req.id or new_object_id(),
                workspace_id=ws_id,
                kind=req.kind,
                slug=req.slug,
                tags=req.tags,
                created_by=actor.user_id,
                created_at=self.clock(),
            )
            s.add(obj)
            s.flush()
            self._audit(s, actor, "object.create", obj.id, kind=req.kind, slug=req.slug)
            return self._object_infos(s, [obj])[0]

    def get_object(self, actor: Actor, object_id: str) -> ObjectInfo:
        self.require(actor, Scope.READ)
        with self.db.session() as s:
            return self._object_infos(s, [self._object(s, object_id, actor)])[0]

    def update_object(self, actor: Actor, object_id: str, req: ObjectUpdate) -> ObjectInfo:
        self.require(actor, Scope.EDIT)
        with self.db.session() as s:
            obj = self._object(s, object_id)
            if req.slug is not None and req.slug != obj.slug:
                if s.scalars(
                    select(db.Object).where(
                        db.Object.workspace_id == obj.workspace_id, db.Object.slug == req.slug
                    )
                ).first():
                    raise ApiError(409, f"slug {req.slug!r} already exists in this workspace")
                obj.slug = req.slug
            if req.tags is not None:
                obj.tags = req.tags
            self._audit(s, actor, "object.update", obj.id, **req.model_dump(exclude_none=True))
            return self._object_infos(s, [obj])[0]

    def delete_object(self, actor: Actor, object_id: str) -> None:
        self.require(actor, Scope.ADMIN)
        with self.db.session() as s:
            obj = self._object(s, object_id)
            obj.deleted_at, obj.deleted_by = self.clock(), actor.user_id
            self._audit(s, actor, "object.trash", obj.id, slug=obj.slug)

    def restore_object(self, actor: Actor, object_id: str) -> ObjectInfo:
        self.require(actor, Scope.ADMIN)
        with self.db.session() as s:
            obj = s.get(db.Object, object_id)
            if obj is None or obj.deleted_at is None:
                raise ApiError(404, "object is not in the trash")
            obj.deleted_at = obj.deleted_by = None
            self._audit(s, actor, "object.restore", obj.id, slug=obj.slug)
            return self._object_infos(s, [obj])[0]

    def list_trash(self, actor: Actor) -> list[ObjectInfo]:
        self.require(actor, Scope.ADMIN)
        with self.db.session() as s:
            objs = list(
                s.scalars(
                    select(db.Object)
                    .where(db.Object.deleted_at.is_not(None))
                    .order_by(db.Object.deleted_at)
                )
            )
            return self._object_infos(s, objs)

    def purge_trash(self) -> int:
        """Delete objects that have been in the trash longer than ``trash_days`` (cron job)."""
        cutoff = self.clock() - timedelta(days=self.settings.trash_days)
        with self.db.session() as s:
            ids = list(
                s.scalars(
                    select(db.Object.id).where(
                        db.Object.deleted_at.is_not(None), db.Object.deleted_at < cutoff
                    )
                )
            )
            if ids:
                s.execute(delete(db.Draft).where(db.Draft.object_id.in_(ids)))
                s.execute(delete(db.Version).where(db.Version.object_id.in_(ids)))
                s.execute(delete(db.Object).where(db.Object.id.in_(ids)))
                self._audit(s, None, "trash.purge", "", objects=ids)
            return len(ids)

    def copy_object(self, actor: Actor, object_id: str, req: ObjectCopy) -> ObjectInfo:
        self.require(actor, Scope.EDIT)
        with self.db.session() as s:
            src = self._object(s, object_id)
            target = s.get(db.Workspace, req.workspace_id)
            if target is None:
                raise ApiError(404, "workspace not found")
            slug = req.slug or src.slug
            if s.scalars(
                select(db.Object).where(db.Object.workspace_id == target.id, db.Object.slug == slug)
            ).first():
                raise ApiError(409, f"slug {slug!r} already exists in the target workspace")
            latest = self._latest(s, src.id)
            if latest is None:
                raise ApiError(422, "object has no version to copy")
            content = s.get(db.Version, latest.id)
            assert content is not None
            now = self.clock()
            obj = db.Object(
                id=new_object_id(),
                workspace_id=target.id,
                kind=src.kind,
                slug=slug,
                tags=list(src.tags),
                created_by=actor.user_id,
                created_at=now,
                copied_from=latest.id,
            )
            s.add(obj)
            s.flush()
            s.add(
                db.Version(
                    id=new_object_id(),
                    object_id=obj.id,
                    semver="1.0.0",
                    name=latest.name,
                    message=f"copied from {src.slug}@{latest.semver}",
                    author=actor.user_id,
                    created_at=now,
                    content_hash=latest.content_hash,
                    content=content.content,
                    parents=[],
                    branch=False,
                    token_id=actor.token_id,
                )
            )
            s.flush()
            self._audit(s, actor, "object.copy", obj.id, source=src.id, version=latest.id)
            return self._object_infos(s, [obj])[0]

    # ------------------------------------------------------------------ versions
    def _canonical(self, s: Session, kind: str, content: dict[str, Any]) -> tuple[str, str]:
        if content.get("schema") == "fileset":
            try:
                fs = FileSetContent.model_validate(content)
            except ValidationError as exc:
                raise ApiError(422, f"invalid content: {exc}") from exc
            missing = [f.path for f in fs.files if s.get(db.Blob, f.sha256) is None]
            if missing:
                raise ApiError(422, f"files not uploaded yet: {', '.join(missing)}")
            text_ = io.canonical_json(fs.model_dump(mode="json", by_alias=True, exclude_none=True))
            return text_, hashlib.sha256(text_.encode()).hexdigest()
        expected = KIND_SCHEMA.get(kind)
        if expected is None:
            raise ApiError(422, f"{kind} versions must use 'fileset' content")
        if content.get("schema") != expected:
            raise ApiError(422, f"{kind} versions need a '{expected}' document")
        try:
            model = io.load(content)
        except (ValueError, TypeError, KeyError) as exc:  # pydantic ValidationError is a ValueError
            raise ApiError(422, f"invalid content: {exc}") from exc
        return io.dump(model), io.content_hash(model)

    def list_versions(self, actor: Actor, object_id: str) -> list[VersionInfo]:
        self.require(actor, Scope.READ)
        with self.db.session() as s:
            self._object(s, object_id, actor)
            rows = list(
                s.scalars(
                    select(db.Version)
                    .options(defer(db.Version.content))
                    .where(db.Version.object_id == object_id)
                    .order_by(db.Version.created_at, db.Version.id)
                )
            )
            users = self._usernames(s, {v.author for v in rows})
            return [_version_info(v, users) for v in rows]

    def create_version(self, actor: Actor, object_id: str, req: VersionCreate) -> VersionInfo:
        self.require(actor, Scope.EDIT)
        with self.db.session() as s:
            obj = self._object(s, object_id)
            text_, digest = self._canonical(s, obj.kind, req.content)
            if req.id and (existing := s.get(db.Version, req.id)) is not None:
                if existing.object_id != object_id or existing.content_hash != digest:
                    raise ApiError(409, f"version id {req.id} exists with different content")
                return _version_info(existing, self._usernames(s, {existing.author}))
            latest = self._latest(s, object_id)
            parents = req.parents if req.parents is not None else ([latest.id] if latest else [])
            known = {
                v
                for v in s.scalars(
                    select(db.Version.id).where(
                        db.Version.object_id == object_id, db.Version.id.in_(parents)
                    )
                )
            }
            if unknown := [p for p in parents if p not in known]:
                raise ApiError(422, f"unknown parent versions: {', '.join(unknown)}")
            if latest is not None and not parents:
                raise ApiError(422, "a version of an object with history needs a parent")
            taken = set(
                s.scalars(select(db.Version.semver).where(db.Version.object_id == object_id))
            )
            if req.semver is not None:
                if req.semver in taken:
                    raise ApiError(409, f"version {req.semver} already exists")
                semver = req.semver
            else:
                first = s.get(db.Version, parents[0]) if parents else None
                semver = _bump_patch(first.semver) if first else "1.0.0"
                while semver in taken:
                    semver = _bump_patch(semver)
            v = db.Version(
                id=req.id or new_object_id(),
                object_id=object_id,
                semver=semver,
                name=req.name,
                message=req.message,
                author=actor.user_id,
                created_at=self.clock(),
                content_hash=digest,
                content=text_,
                parents=parents,
                branch=latest is not None and latest.id not in parents,
                token_id=actor.token_id,
            )
            s.add(v)
            s.flush()
            self._audit(
                s, actor, "version.create", v.id, object=object_id, semver=semver, branch=v.branch
            )
            return _version_info(v, {actor.user_id: actor.username})

    def get_version(self, actor: Actor, version_id: str) -> VersionContent:
        import json

        self.require(actor, Scope.READ)
        with self.db.session() as s:
            v = s.get(db.Version, version_id)
            if v is None:
                raise ApiError(404, "version not found")
            self._object(s, v.object_id, actor)
            info = _version_info(v, self._usernames(s, {v.author}))
            return VersionContent(**info.model_dump(), content=json.loads(v.content))

    # ------------------------------------------------------------------ drafts
    def get_draft(self, actor: Actor, object_id: str) -> DraftOut:
        self.require(actor, Scope.READ)
        with self.db.session() as s:
            self._object(s, object_id)
            d = s.get(db.Draft, (object_id, actor.user_id))
            if d is None:
                raise ApiError(404, "no draft")
            return DraftOut(content=d.content, updated_at=d.updated_at)

    def put_draft(self, actor: Actor, object_id: str, content: dict[str, Any]) -> DraftOut:
        self.require(actor, Scope.EDIT)
        with self.db.session() as s:
            self._object(s, object_id)
            d = s.get(db.Draft, (object_id, actor.user_id))
            now = self.clock()
            if d is None:
                d = db.Draft(
                    object_id=object_id, user_id=actor.user_id, content=content, updated_at=now
                )
                s.add(d)
            else:
                d.content, d.updated_at = content, now
            self._audit(s, actor, "draft.save", object_id)
            return DraftOut(content=d.content, updated_at=d.updated_at)

    # ------------------------------------------------------------------ blobs
    def blob_size(self, actor: Actor, sha: str) -> int:
        self.require(actor, Scope.READ)
        with self.db.session() as s:
            b = s.get(db.Blob, sha)
            if b is None:
                raise ApiError(404, "blob not found")
            return b.size

    def read_blob(self, sha: str, start: int, length: int) -> Iterator[bytes]:
        return self.store.read(blob_key(sha), start, length)

    def _upload_info(self, s: Session, up: db.Upload) -> UploadInfo:
        received = sorted(
            s.scalars(select(db.UploadPart.number).where(db.UploadPart.upload_id == up.id))
        )
        return UploadInfo(
            id=up.id,
            sha256=up.sha256,
            size=up.size,
            part_size=up.part_size,
            parts=self._part_count(up),
            received=received,
            status="complete" if up.completed_at else "open",
        )

    @staticmethod
    def _part_count(up: db.Upload) -> int:
        return max(1, math.ceil(up.size / up.part_size)) if up.part_size else 1

    def create_upload(self, actor: Actor, req: UploadCreate) -> UploadInfo:
        self.require(actor, Scope.EDIT)
        part_size = self.settings.max_part_bytes
        parts = max(1, math.ceil(req.size / part_size))
        with self.db.session() as s:
            if s.get(db.Blob, req.sha256) is not None:
                return UploadInfo(
                    id=None,
                    sha256=req.sha256,
                    size=req.size,
                    part_size=part_size,
                    parts=parts,
                    received=[],
                    status="exists",
                )
            up = s.scalars(  # resume an unfinished upload of the same file
                select(db.Upload).where(
                    db.Upload.sha256 == req.sha256,
                    db.Upload.size == req.size,
                    db.Upload.created_by == actor.user_id,
                    db.Upload.completed_at.is_(None),
                )
            ).first()
            if up is None:
                up = db.Upload(
                    id=new_object_id(),
                    sha256=req.sha256,
                    size=req.size,
                    part_size=part_size,
                    created_by=actor.user_id,
                    created_at=self.clock(),
                )
                s.add(up)
                s.flush()
                self._audit(s, actor, "upload.create", up.id, sha256=req.sha256, size=req.size)
            return self._upload_info(s, up)

    def _own_upload(self, s: Session, actor: Actor, upload_id: str) -> db.Upload:
        up = s.get(db.Upload, upload_id)
        if up is None or up.created_by != actor.user_id:
            raise ApiError(404, "upload not found")
        if up.completed_at is not None:
            raise ApiError(409, "upload already complete")
        return up

    def get_upload(self, actor: Actor, upload_id: str) -> UploadInfo:
        self.require(actor, Scope.EDIT)
        with self.db.session() as s:
            up = s.get(db.Upload, upload_id)
            if up is None or up.created_by != actor.user_id:
                raise ApiError(404, "upload not found")
            return self._upload_info(s, up)

    def put_part(
        self, actor: Actor, upload_id: str, number: int, data: bytes, sha: str | None
    ) -> UploadInfo:
        self.require(actor, Scope.EDIT)
        with self.db.session() as s:
            up = self._own_upload(s, actor, upload_id)
            count = self._part_count(up)
            if not 0 <= number < count:
                raise ApiError(400, f"part number must be 0..{count - 1}")
            expected = up.part_size if number < count - 1 else up.size - up.part_size * (count - 1)
            if len(data) != expected:
                raise ApiError(400, f"part {number} must be {expected} bytes, got {len(data)}")
            digest = hashlib.sha256(data).hexdigest()
            if sha is None or sha.lower() != digest:
                raise ApiError(400, f"checksum mismatch in part {number}")
            self.store.put_bytes(part_key(up.id, number), data)
            row = s.get(db.UploadPart, (up.id, number))
            if row is None:
                s.add(db.UploadPart(upload_id=up.id, number=number, size=len(data), sha256=digest))
            else:
                row.size, row.sha256 = len(data), digest
            s.flush()
            return self._upload_info(s, up)

    def complete_upload(self, actor: Actor, upload_id: str) -> UploadInfo:
        self.require(actor, Scope.EDIT)
        with self.db.session() as s:
            up = self._own_upload(s, actor, upload_id)
            count = self._part_count(up)
            have = set(
                s.scalars(select(db.UploadPart.number).where(db.UploadPart.upload_id == up.id))
            )
            if missing := sorted(set(range(count)) - have):
                raise ApiError(409, f"missing parts: {missing}")
            if s.get(db.Blob, up.sha256) is None:
                self._assemble(up, count)
                s.add(
                    db.Blob(
                        sha256=up.sha256,
                        size=up.size,
                        created_by=actor.user_id,
                        created_at=self.clock(),
                    )
                )
            for n in range(count):
                self.store.delete(part_key(up.id, n))
            s.execute(delete(db.UploadPart).where(db.UploadPart.upload_id == up.id))
            up.completed_at = self.clock()
            self._audit(s, actor, "blob.upload", up.sha256, size=up.size, upload=up.id)
            return self._upload_info(s, up)

    def _assemble(self, up: db.Upload, count: int) -> None:
        digest = hashlib.sha256()
        tmp_dir = self.settings.upload_tmp_dir
        with tempfile.NamedTemporaryFile(dir=tmp_dir, delete=False) as f:
            tmp = Path(f.name)
            try:
                for n in range(count):
                    for chunk in self.store.read(part_key(up.id, n)):
                        digest.update(chunk)
                        f.write(chunk)
                f.close()
                if digest.hexdigest() != up.sha256:
                    for n in range(count):  # parts were fine but are not this file: start over
                        self.store.delete(part_key(up.id, n))
                    raise ApiError(400, "checksum of the assembled file does not match")
                self.store.put_file(blob_key(up.sha256), tmp)
            finally:
                tmp.unlink(missing_ok=True)

    # ------------------------------------------------------------------ audit & status
    def audit(
        self, actor: Actor, limit: int = 100, offset: int = 0, action: str | None = None
    ) -> list[AuditInfo]:
        self.require(actor, Scope.ADMIN)
        with self.db.session() as s:
            q = select(db.AuditEntry).order_by(db.AuditEntry.id.desc())
            if action:
                q = q.where(db.AuditEntry.action == action)
            rows = list(s.scalars(q.limit(limit).offset(offset)))
            users = self._usernames(s, {r.user_id for r in rows if r.user_id})
            return [
                AuditInfo(
                    id=r.id,
                    at=r.at,
                    user=users.get(r.user_id, r.user_id) if r.user_id else None,
                    token_id=r.token_id,
                    client=r.client,
                    action=r.action,
                    target=r.target,
                    detail=r.detail,
                )
                for r in rows
            ]

    def status(self) -> Status:
        try:
            with self.db.session() as s:
                s.execute(text("SELECT 1"))
            database = True
        except Exception:
            database = False
        try:
            usage = shutil.disk_usage(self.settings.data_path)
            used = usage.used / usage.total
        except OSError:
            used = 0.0
        level = (
            "critical"
            if used >= 0.95
            else "high"
            if used >= 0.85
            else "warn"
            if used >= 0.7
            else "ok"
        )
        return Status(
            version=__version__,
            database=database,
            blobs=self.store.ping(),
            disk_used=round(used, 4),
            disk_level=level,
        )
