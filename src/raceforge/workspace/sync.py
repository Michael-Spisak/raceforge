"""Offline-first workspace: local SQLite + blob cache, synced with the team backend (spec 0006).

Everything is saved locally first (objects and versions get client-made UUIDv7 ids), then pushed.
Two versions with the same parent are *both* kept as branches and reported as a conflict.
"""

import hashlib
import json
import shutil
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from raceforge.backend.models import FileEntry, FileSetContent, TokenPair, UserInfo, WorkspaceInfo
from raceforge.core import io
from raceforge.core.ids import new_object_id
from raceforge.workspace.client import (
    BackendClient,
    BackendError,
    ClientFactory,
    OfflineError,
    default_factory,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS objects (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, kind TEXT NOT NULL, slug TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '[]', pending INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS versions (
  id TEXT PRIMARY KEY, object_id TEXT NOT NULL, semver TEXT, name TEXT, message TEXT NOT NULL,
  author TEXT NOT NULL, created_at TEXT NOT NULL, content_hash TEXT NOT NULL,
  parents TEXT NOT NULL, branch INTEGER NOT NULL DEFAULT 0, content TEXT,
  pending INTEGER NOT NULL DEFAULT 0, error TEXT);
CREATE TABLE IF NOT EXISTS pending_blobs (sha256 TEXT PRIMARY KEY);
"""


class _Rows:
    def __init__(self, rows: list[sqlite3.Row]) -> None:
        self.rows = rows

    def fetchone(self) -> sqlite3.Row | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[sqlite3.Row]:
        return self.rows


class LocalVersion(BaseModel):
    id: str
    object_id: str
    semver: str | None  # None until the backend has numbered it
    name: str | None
    message: str
    author: str
    created_at: datetime
    content_hash: str
    parents: list[str]
    branch: bool
    pending: bool
    error: str | None = None


class LocalObject(BaseModel):
    id: str
    workspace_id: str
    kind: str
    slug: str
    tags: list[str]
    pending: bool
    latest: LocalVersion | None


class Conflict(BaseModel):
    object_id: str
    slug: str
    parent: str | None
    versions: list[LocalVersion]


class SyncResult(BaseModel):
    pushed: int = 0
    pulled: int = 0
    conflicts: list[Conflict] = []
    errors: list[str] = []


class WorkspaceStatus(BaseModel):
    logged_in: bool
    online: bool
    server_url: str | None
    user: UserInfo | None
    workspace: WorkspaceInfo | None
    pending: int
    conflicts: int
    last_sync: datetime | None


def content_digest(content: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Validate content exactly like the backend and return (canonical content, hash)."""
    if content.get("schema") == "fileset":
        fs = FileSetContent.model_validate(content)
        data = fs.model_dump(mode="json", by_alias=True, exclude_none=True)
        return data, hashlib.sha256(io.canonical_json(data).encode()).hexdigest()
    model = io.load(content)
    return io.to_jsonable(model), io.content_hash(model)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Workspace:
    def __init__(self, root: Path, factory: ClientFactory = default_factory) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.factory = factory
        self._db = sqlite3.connect(root / "workspace.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._lock = threading.RLock()
        self._client: BackendClient | None = None
        self._online = False
        self._stop = threading.Event()

    # ------------------------------------------------------------ local store
    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._db:
            yield self._db

    def _q(self, sql: str, args: Sequence[Any] = ()) -> "_Rows":
        """Read query; the connection is shared by request threads and the sync thread."""
        with self._lock:
            return _Rows(self._db.execute(sql, args).fetchall())

    def _get(self, key: str) -> str | None:
        row = self._q("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def _set(self, key: str, value: str | None) -> None:
        with self._tx() as c:
            if value is None:
                c.execute("DELETE FROM kv WHERE key = ?", (key,))
            else:
                c.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, value))

    def blob_path(self, sha: str) -> Path:
        return self.root / "blobs" / sha[:2] / sha

    @property
    def user(self) -> UserInfo | None:
        raw = self._get("user")
        return None if raw is None else UserInfo.model_validate_json(raw)

    @property
    def workspace_id(self) -> str | None:
        return self._get("workspace_id")

    # ------------------------------------------------------------ connection
    def client(self) -> BackendClient:
        url = self._get("server_url")
        if url is None or self._get("access") is None:
            raise BackendError(401, "not logged in")
        if self._client is None:
            self._client = BackendClient(
                url, self._get("access"), self._get("refresh"), self._save_tokens, self.factory
            )
        return self._client

    def _save_tokens(self, pair: TokenPair) -> None:
        self._set("access", pair.access_token)
        self._set("refresh", pair.refresh_token)
        self._set("user", pair.user.model_dump_json())

    def login(self, server_url: str, username: str, password: str, totp: str | None) -> UserInfo:
        if self._client is not None:
            self._client.close()
        self._client = BackendClient(server_url, factory=self.factory, on_tokens=self._save_tokens)
        try:
            pair = self._client.login(username, password, totp)
        except OfflineError:
            self._online = False
            raise
        self._online = True
        if self._get("server_url") not in (None, server_url.rstrip("/")) or (
            self.user is not None and self.user.id != pair.user.id
        ):
            self._reset_cache()  # another server or account: never mix their data
        self._set("server_url", server_url.rstrip("/"))
        self._save_tokens(pair)
        return pair.user

    def _reset_cache(self) -> None:
        with self._tx() as c:
            for table in ("workspaces", "objects", "versions", "pending_blobs"):
                c.execute(f"DELETE FROM {table}")
            c.execute("DELETE FROM kv WHERE key IN ('workspace_id', 'last_sync')")

    def logout(self) -> None:
        if self._client is not None:
            with suppress(OfflineError, BackendError):
                self._client.logout()
            self._client.close()
            self._client = None
        for key in ("access", "refresh"):
            self._set(key, None)

    def totp_verify(self, code: str) -> UserInfo:
        user = self.client().totp_verify(code)
        self._set("user", user.model_dump_json())
        return user

    def check_online(self) -> bool:
        if self._get("server_url") is None or self._get("access") is None:
            self._online = False
            return False
        try:
            self.client().status()
            self._online = True
        except (OfflineError, BackendError):
            self._online = False
        return self._online

    def status(self, probe: bool = False) -> WorkspaceStatus:
        online = self.check_online() if probe else self._online
        ws = None
        if (wid := self.workspace_id) is not None:
            row = self._q("SELECT * FROM workspaces WHERE id = ?", (wid,)).fetchone()
            if row is not None:
                ws = self._ws_info(row)
        pending = self._q(
            "SELECT (SELECT count(*) FROM versions WHERE pending = 1)"
            " + (SELECT count(*) FROM objects WHERE pending = 1)"
        ).fetchall()[0][0]
        last = self._get("last_sync")
        return WorkspaceStatus(
            logged_in=self._get("access") is not None,
            online=online,
            server_url=self._get("server_url"),
            user=self.user,
            workspace=ws,
            pending=int(pending),
            conflicts=len(self.conflicts()),
            last_sync=datetime.fromisoformat(last) if last else None,
        )

    # ------------------------------------------------------------ workspaces
    @staticmethod
    def _ws_info(row: sqlite3.Row) -> WorkspaceInfo:
        return WorkspaceInfo(
            id=row["id"], name=row["name"], created_at=datetime.fromisoformat(row["created_at"])
        )

    def workspaces(self) -> list[WorkspaceInfo]:
        try:
            remote = self.client().workspaces()
            self._online = True
            with self._tx() as c:
                c.execute("DELETE FROM workspaces")
                c.executemany(
                    "INSERT INTO workspaces VALUES (?, ?, ?)",
                    [(w.id, w.name, w.created_at.isoformat()) for w in remote],
                )
            return remote
        except OfflineError:
            self._online = False
        rows = self._q("SELECT * FROM workspaces ORDER BY name").fetchall()
        return [
            WorkspaceInfo(id=r["id"], name=r["name"], created_at=datetime.now(UTC)) for r in rows
        ]

    def create_workspace(self, name: str) -> WorkspaceInfo:
        ws = self.client().create_workspace(name)
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO workspaces VALUES (?, ?, ?)",
                (ws.id, ws.name, ws.created_at.isoformat()),
            )
        return ws

    def select(self, workspace_id: str) -> None:
        self._set("workspace_id", workspace_id)

    # ------------------------------------------------------------ objects & versions
    @staticmethod
    def _version(row: sqlite3.Row) -> LocalVersion:
        return LocalVersion(
            id=row["id"],
            object_id=row["object_id"],
            semver=row["semver"],
            name=row["name"],
            message=row["message"],
            author=row["author"],
            created_at=datetime.fromisoformat(row["created_at"]),
            content_hash=row["content_hash"],
            parents=json.loads(row["parents"]),
            branch=bool(row["branch"]),
            pending=bool(row["pending"]),
            error=row["error"],
        )

    def _latest(self, object_id: str) -> LocalVersion | None:
        row = self._q(
            "SELECT * FROM versions WHERE object_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (object_id,),
        ).fetchone()
        return None if row is None else self._version(row)

    def objects(self, kind: str | None = None) -> list[LocalObject]:
        wid = self.workspace_id
        if wid is None:
            return []
        q, args = "SELECT * FROM objects WHERE workspace_id = ?", [wid]
        if kind:
            q += " AND kind = ?"
            args.append(kind)
        return [
            LocalObject(
                id=r["id"],
                workspace_id=r["workspace_id"],
                kind=r["kind"],
                slug=r["slug"],
                tags=json.loads(r["tags"]),
                pending=bool(r["pending"]),
                latest=self._latest(r["id"]),
            )
            for r in self._q(q + " ORDER BY slug", args).fetchall()
        ]

    def history(self, object_id: str) -> list[LocalVersion]:
        rows = self._q(
            "SELECT * FROM versions WHERE object_id = ? ORDER BY created_at, id", (object_id,)
        ).fetchall()
        return [self._version(r) for r in rows]

    def save(
        self,
        kind: str,
        slug: str,
        content: dict[str, Any],
        message: str = "",
        name: str | None = None,
        sync: bool = True,
    ) -> LocalVersion:
        """Save content as a new version of the object ``slug`` (created if needed)."""
        wid, user = self.workspace_id, self.user
        if wid is None or user is None:
            raise BackendError(409, "log in and pick a workspace first")
        canonical, digest = content_digest(content)
        with self._tx() as c:
            row = c.execute(
                "SELECT * FROM objects WHERE workspace_id = ? AND slug = ?", (wid, slug)
            ).fetchone()
            if row is None:
                oid = new_object_id()
                c.execute(
                    "INSERT INTO objects (id, workspace_id, kind, slug, pending)"
                    " VALUES (?,?,?,?,1)",
                    (oid, wid, kind, slug),
                )
            elif row["kind"] != kind:
                raise BackendError(409, f"{slug!r} is a {row['kind']}, not a {kind}")
            else:
                oid = row["id"]
            latest = self._latest(oid)
            vid = new_object_id()
            c.execute(
                "INSERT INTO versions (id, object_id, semver, name, message, author, created_at,"
                " content_hash, parents, branch, content, pending)"
                " VALUES (?,?,?,?,?,?,?,?,?,0,?,1)",
                (
                    vid,
                    oid,
                    None,
                    name,
                    message,
                    user.username,
                    datetime.now(UTC).isoformat(),
                    digest,
                    json.dumps([latest.id] if latest else []),
                    io.canonical_json(canonical),
                ),
            )
        if sync:
            with suppress(OfflineError, BackendError):
                self.sync()
        version = self.history(oid)
        return next(v for v in version if v.id == vid)

    def save_files(
        self, kind: str, slug: str, files: list[Path], message: str = "", entry: str | None = None
    ) -> LocalVersion:
        """Save files (e.g. a controller + its params YAML) as a fileset version (bytes → blobs)."""
        entries: list[FileEntry] = []
        for path in files:
            sha = file_sha256(path)
            dest = self.blob_path(sha)
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
            with self._tx() as c:
                c.execute("INSERT OR IGNORE INTO pending_blobs VALUES (?)", (sha,))
            entries.append(FileEntry(path=path.name, sha256=sha, size=path.stat().st_size))
        content = FileSetContent(files=entries, entry=entry or files[0].name)
        return self.save(kind, slug, content.model_dump(mode="json", by_alias=True), message)

    def version_content(self, version_id: str) -> dict[str, Any]:
        row = self._q("SELECT content FROM versions WHERE id = ?", (version_id,)).fetchone()
        if row is None:
            raise BackendError(404, "unknown version")
        if row["content"] is None:  # fetched lazily
            remote = self.client().version(version_id)
            text = io.canonical_json(remote.content)
            with self._tx() as c:
                c.execute("UPDATE versions SET content = ? WHERE id = ?", (text, version_id))
            return remote.content
        return json.loads(row["content"])

    def blob(self, sha: str) -> Path:
        """Local path of a blob, downloaded on first use (resumable) and cached."""
        path = self.blob_path(sha)
        if not path.exists():
            self.client().download_blob(sha, path)
        return path

    # ------------------------------------------------------------ sync
    def conflicts(self) -> list[Conflict]:
        rows = self._q(
            "SELECT v.*, o.slug FROM versions v JOIN objects o ON o.id = v.object_id"
            " WHERE o.workspace_id = ? ORDER BY v.created_at",
            (self.workspace_id or "",),
        ).fetchall()
        children: dict[tuple[str, str | None], list[LocalVersion]] = {}
        slugs: dict[str, str] = {}
        for r in rows:
            v = self._version(r)
            slugs[v.object_id] = r["slug"]
            children.setdefault((v.object_id, v.parents[0] if v.parents else None), []).append(v)
        return [
            Conflict(object_id=oid, slug=slugs[oid], parent=parent, versions=vs)
            for (oid, parent), vs in children.items()
            if len(vs) > 1
        ]

    def sync(self) -> SyncResult:
        with self._lock:
            result = SyncResult()
            client = self.client()
            try:
                pushed = self._push(client, result)
                self._pull(client, result, pushed)
            except OfflineError:
                self._online = False
                raise
            self._online = True
            self._set("last_sync", datetime.now(UTC).isoformat())
            result.conflicts = self.conflicts()
            return result

    def _push(self, client: BackendClient, result: SyncResult) -> set[str]:
        pushed: set[str] = set()
        for r in self._q("SELECT * FROM objects WHERE pending = 1").fetchall():
            try:
                client.create_object(r["workspace_id"], r["id"], r["kind"], r["slug"])
            except BackendError as exc:
                result.errors.append(f"{r['slug']}: {exc.detail}")
                continue
            with self._tx() as c:
                c.execute("UPDATE objects SET pending = 0 WHERE id = ?", (r["id"],))
        for (sha,) in self._q("SELECT sha256 FROM pending_blobs").fetchall():
            client.upload_blob(self.blob_path(sha), sha)
            with self._tx() as c:
                c.execute("DELETE FROM pending_blobs WHERE sha256 = ?", (sha,))
        rows = self._q(
            "SELECT v.* FROM versions v JOIN objects o ON o.id = v.object_id"
            " WHERE v.pending = 1 AND o.pending = 0 ORDER BY v.created_at"
        ).fetchall()
        for r in rows:
            try:
                info = client.create_version(
                    r["object_id"],
                    r["id"],
                    json.loads(r["content"]),
                    json.loads(r["parents"]),
                    r["message"],
                    r["name"],
                )
            except BackendError as exc:
                with self._tx() as c:
                    c.execute("UPDATE versions SET error = ? WHERE id = ?", (exc.detail, r["id"]))
                result.errors.append(f"version {r['id']}: {exc.detail}")
                continue
            with self._tx() as c:
                c.execute(
                    "UPDATE versions SET semver = ?, branch = ?, created_at = ?, pending = 0,"
                    " error = NULL WHERE id = ?",
                    (info.semver, int(info.branch), info.created_at.isoformat(), info.id),
                )
            result.pushed += 1
            pushed.add(r["object_id"])
        return pushed

    def _pull(self, client: BackendClient, result: SyncResult, pushed: set[str]) -> None:
        wid = self.workspace_id
        if wid is None:
            return
        for obj in client.objects(wid):
            with self._tx() as c:
                c.execute(
                    "INSERT INTO objects (id, workspace_id, kind, slug, tags, pending)"
                    " VALUES (?,?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET slug = excluded.slug,"
                    " tags = excluded.tags, pending = 0",
                    (obj.id, obj.workspace_id, obj.kind, obj.slug, json.dumps(obj.tags)),
                )
            known = {
                r[0]
                for r in self._q(
                    "SELECT id FROM versions WHERE object_id = ?", (obj.id,)
                ).fetchall()
            }
            # Any new version becomes the newest one, so a known latest means nothing to pull —
            # unless we pushed to this object just now (our push may hide someone else's).
            if obj.latest is None or (obj.latest.id in known and obj.id not in pushed):
                continue
            versions = client.versions(obj.id)
            for v in versions:
                if v.id in known:
                    continue
                with self._tx() as c:
                    c.execute(
                        "INSERT INTO versions (id, object_id, semver, name, message, author,"
                        " created_at, content_hash, parents, branch, content, pending)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,NULL,0)",
                        (
                            v.id,
                            v.object_id,
                            v.semver,
                            v.name,
                            v.message,
                            v.author,
                            v.created_at.isoformat(),
                            v.content_hash,
                            json.dumps(v.parents),
                            int(v.branch),
                        ),
                    )
                result.pulled += 1

    # ------------------------------------------------------------ background
    def start_background(self, interval_s: float = 30.0) -> None:
        def loop() -> None:
            while not self._stop.wait(interval_s):
                if self._get("access") and self.workspace_id:
                    with suppress(OfflineError, BackendError):
                        self.sync()

        threading.Thread(target=loop, name="workspace-sync", daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        if self._client is not None:
            self._client.close()
        self._db.close()
