"""Engine side of the scan viewer (spec 0009): find TrackScout passes and serve them in 3D."""

import base64
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from raceforge.api.models import ScanDetail, ScanMesh, ScanPassRef, ScanSegment, ScanTrack
from raceforge.api.workspace import WorkspaceApi
from raceforge.capture.tscan import ARKIT_CLASSES, TscanError, TscanPass
from raceforge.workspace.client import BackendError, OfflineError
from raceforge.workspace.sync import file_sha256

MAX_TRAJECTORY = 2000
CACHE = 8


class ScanNotFoundError(KeyError):
    pass


class ScanApi:
    def __init__(self, workspace: WorkspaceApi) -> None:
        self.wsapi = workspace
        self.opened: dict[str, Path] = {}  # sha → local .tscan opened by path this session
        self._lock = threading.Lock()
        self._details: OrderedDict[str, ScanDetail] = OrderedDict()
        self._meshes: OrderedDict[tuple[str, int], ScanMesh] = OrderedDict()

    # ------------------------------------------------------------------ listing
    def tracks(self) -> list[ScanTrack]:
        tracks: list[ScanTrack] = []
        ws = self.wsapi.ws
        if ws.workspace_id is not None:
            for obj in ws.objects("capture"):
                if obj.latest is None:
                    continue
                files = ws.version_content(obj.latest.id).get("files", [])
                refs = [
                    ScanPassRef(
                        sha256=f["sha256"], name=f["path"], size=f["size"], source="workspace"
                    )
                    for f in files
                ]
                tracks.append(
                    ScanTrack(name=obj.slug, slug=obj.slug, version=obj.latest.semver, passes=refs)
                )
        laptop: dict[str, list[ScanPassRef]] = {}
        for p in self.wsapi.inbox.list():
            if p.state in ("receiving", "uploaded"):  # uploaded passes are in the workspace list
                continue
            laptop.setdefault(p.project, []).append(
                ScanPassRef(
                    sha256=p.sha256,
                    name=f"{p.id}.tscan",
                    size=p.size,
                    source="laptop",
                    pass_type=p.pass_type,
                    created_at=p.created_at,
                )
            )
        tracks += [ScanTrack(name=name, passes=refs) for name, refs in laptop.items()]
        if self.opened:
            refs = [
                ScanPassRef(sha256=sha, name=path.name, size=path.stat().st_size, source="file")
                for sha, path in self.opened.items()
                if path.exists()
            ]
            tracks.append(ScanTrack(name="files", passes=refs))
        return tracks

    def open_file(self, path: str) -> ScanPassRef:
        p = Path(path).expanduser()
        if p.suffix != ".tscan" or not p.is_file():
            raise ValueError(f"not a .tscan file: {path}")
        with TscanPass(p) as tp:  # validates schema + checksums
            m = tp.manifest
            ref = ScanPassRef(
                sha256=file_sha256(p),
                name=p.name,
                size=p.stat().st_size,
                source="file",
                pass_type=m.pass_.type,
                created_at=m.created_at.isoformat(),
            )
        self.opened[ref.sha256] = p
        return ref

    # ------------------------------------------------------------------ resolving
    def _path(self, sha: str) -> Path:
        if sha in self.opened:
            return self.opened[sha]
        for p in self.wsapi.inbox.list():
            if p.sha256 == sha and self.wsapi.inbox.path(p.id).exists():
                return self.wsapi.inbox.path(p.id)
        ws = self.wsapi.ws
        if ws.workspace_id is not None:
            for obj in ws.objects("capture"):
                if obj.latest is None:
                    continue
                files = ws.version_content(obj.latest.id).get("files", [])
                if any(f["sha256"] == sha for f in files):
                    try:
                        return ws.blob(sha)
                    except OfflineError as exc:
                        raise OfflineError("not downloaded yet (backend offline)") from exc
        raise ScanNotFoundError(sha)

    def _cached[K, V](self, cache: "OrderedDict[K, V]", key: K, value: V) -> V:
        with self._lock:
            cache[key] = value
            cache.move_to_end(key)
            while len(cache) > CACHE:
                cache.popitem(last=False)
        return value

    # ------------------------------------------------------------------ detail & mesh
    def detail(self, sha: str) -> ScanDetail:
        if sha in self._details:
            return self._details[sha]
        with TscanPass(self._path(sha)) as tp:
            m = tp.manifest
            t, poses = tp.poses(include_discarded=True)
            kept = tp.kept_mask()
            seg = tp.frame_segments()
            step = max(1, int(np.ceil(len(t) / MAX_TRAJECTORY)))
            pos = poses[::step, :3, 3]
            points: list[NDArray[Any]] = [pos]
            for s in m.segments:
                mesh = tp.mesh(s.index)
                if mesh is not None and len(mesh.vertices):
                    points.append(mesh.vertices)
            allp = np.concatenate(points) if any(len(p) for p in points) else np.zeros((1, 3))
            lo, hi = allp.min(axis=0), allp.max(axis=0)
            detail = ScanDetail(
                sha256=sha,
                summary=tp.summary(),
                segments=[
                    ScanSegment(
                        index=s.index,
                        start_s=s.start_s,
                        end_s=s.end_s,
                        frames=s.frames,
                        discarded=[(a, b) for a, b in s.discarded],
                    )
                    for s in m.segments
                ],
                trajectory=[(float(x), float(y), float(z)) for x, y, z in pos],
                trajectory_kept=[bool(k) for k in kept[::step]],
                trajectory_segment=[int(s) for s in seg[::step]],
                bounds=(
                    (float(lo[0]), float(lo[1]), float(lo[2])),
                    (float(hi[0]), float(hi[1]), float(hi[2])),
                ),
            )
        return self._cached(self._details, sha, detail)

    def mesh(self, sha: str, max_faces: int = 300_000) -> ScanMesh:
        key = (sha, max_faces)
        if key in self._meshes:
            return self._meshes[key]
        verts: list[NDArray[Any]] = []
        faces: list[NDArray[Any]] = []
        classes: list[NDArray[Any]] = []
        offset = 0
        with TscanPass(self._path(sha)) as tp:
            for s in tp.manifest.segments:
                mesh = tp.mesh(s.index)
                if mesh is None:
                    continue
                verts.append(mesh.vertices.astype("<f4"))
                faces.append(mesh.faces.astype("<u4") + offset)
                classes.append(mesh.classification.astype("u1"))
                offset += len(mesh.vertices)
        v = np.concatenate(verts) if verts else np.zeros((0, 3), "<f4")
        f = np.concatenate(faces) if faces else np.zeros((0, 3), "<u4")
        c = np.concatenate(classes) if classes else np.zeros(0, "u1")
        total = len(f)
        if total > max_faces > 0:
            keep = np.linspace(0, total - 1, max_faces).astype(np.int64)
            f, c = f[keep], c[keep]
        result = ScanMesh(
            sha256=sha,
            vertices=len(v),
            faces=len(f),
            total_faces=total,
            positions_b64=base64.b64encode(np.ascontiguousarray(v, "<f4").tobytes()).decode(),
            indices_b64=base64.b64encode(np.ascontiguousarray(f, "<u4").tobytes()).decode(),
            classes_b64=base64.b64encode(np.ascontiguousarray(c, "u1").tobytes()).decode(),
            classes=list(ARKIT_CLASSES),
        )
        return self._cached(self._meshes, key, result)


__all__ = ["BackendError", "ScanApi", "ScanNotFoundError", "TscanError"]
