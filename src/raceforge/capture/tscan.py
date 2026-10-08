"""Reader for TrackScout `.tscan` v1 passes (spec 0007, human-owned format).

A pass is a ZIP64 archive: ``manifest.json`` + binary streams. ARKit's world frame (Y-up,
right-handed) is converted to RaceForge's Z-up SI frame; camera axes keep the ARKit convention
(x right, y up, -z view).
"""

import hashlib
import json
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

FRAME_DTYPE = np.dtype(
    [
        ("t", "<f8"),
        ("segment", "<u2"),
        ("tracking", "u1"),
        ("has_depth", "u1"),
        ("pose", "<f4", (16,)),
        ("intrinsics", "<f4", (9,)),
        ("exposure_s", "<f4"),
    ]
)
IMU_DTYPE = np.dtype(
    [
        ("t", "<f8"),
        ("gravity", "<f4", (3,)),
        ("acceleration", "<f4", (3,)),
        ("rotation_rate", "<f4", (3,)),
    ]
)
# ARKit world (x right, y up, z towards the viewer) → RaceForge world (x, y forward, z up).
ARKIT_TO_RF = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=np.float64)
ARKIT_CLASSES = ("none", "wall", "floor", "ceiling", "table", "seat", "window", "door")
TRACKING = ("not_available", "limited", "normal")

Floats = NDArray[np.float64]


class TscanError(ValueError):
    """The file is not a valid `.tscan` v1 pass."""


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppInfo(_M):
    name: str
    version: str


class DeviceInfo(_M):
    model: str
    system: str


class ProjectInfo(_M):
    id: str
    name: str


class Conditions(_M):
    lights: str
    doors: str
    note: str


class PassInfo(_M):
    id: str
    type: Literal["walkthrough", "high", "low", "detail", "gap_fill"]
    conditions: Conditions


class WorldMapInfo(_M):
    id: str | None
    included: bool
    aligned: bool
    aligned_at_s: float | None = None


class DepthInfo(_M):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    every_nth_frame: int = Field(ge=1)


class Segment(_M):
    index: int
    start_s: float
    end_s: float
    frames: int
    video: str | None = None
    depth: str
    mesh: str | None = None
    discarded: list[tuple[float, float]]


class FileInfo(_M):
    sha256: str
    size: int


class Manifest(_M):
    schema_: Literal["tscan"] = Field(alias="schema")
    schema_version: Literal[1]
    app: AppInfo
    device: DeviceInfo
    project: ProjectInfo
    pass_: PassInfo = Field(alias="pass")
    quality: Literal["maximum", "high", "economy"]
    created_at: datetime
    coordinate_frame: Literal["arkit"]
    world_map: WorldMapInfo
    depth: DepthInfo
    segments: list[Segment]
    roomplan: bool
    files: dict[str, FileInfo]


@dataclass(frozen=True)
class Frame:
    index: int
    t: float
    segment: int
    tracking: str
    pose: Floats  # 4x4 camera → RaceForge world (Z-up)
    pose_arkit: Floats  # 4x4 camera → ARKit world (as recorded)
    intrinsics: Floats  # 3x3, pixels of the RGB image
    exposure_s: float
    depth: NDArray[np.float32] | None  # HxW metres
    confidence: NDArray[np.uint8] | None  # HxW, 0 low … 2 high


@dataclass(frozen=True)
class Mesh:
    vertices: Floats  # Nx3, RaceForge world
    faces: NDArray[np.uint32]  # Mx3
    classification: NDArray[np.uint8]  # M, index into ARKIT_CLASSES


def arkit_to_rf(pose: Floats) -> Floats:
    return ARKIT_TO_RF @ pose


class TscanPass:
    """An opened `.tscan` pass. Use as a context manager or call :meth:`close`."""

    def __init__(self, path: Path | str, verify: bool = True) -> None:
        self.path = Path(path)
        try:
            self._zip = zipfile.ZipFile(self.path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise TscanError(f"{self.path.name}: not a ZIP archive ({exc})") from exc
        try:
            raw = json.loads(self._zip.read("manifest.json"))
        except KeyError as exc:
            raise TscanError("manifest.json missing") from exc
        if raw.get("schema") != "tscan":
            raise TscanError("not a TrackScout pass (schema != 'tscan')")
        if raw.get("schema_version") != 1:
            raise TscanError(f"unsupported .tscan version {raw.get('schema_version')}")
        self.manifest = Manifest.model_validate(raw)
        if verify:
            self.verify()
        self._frames = np.frombuffer(self._zip.read("frames.bin"), dtype=FRAME_DTYPE)

    def __enter__(self) -> "TscanPass":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._zip.close()

    def verify(self) -> None:
        """Checks presence, size and SHA-256 of every file listed in the manifest."""
        names = set(self._zip.namelist())
        for path, info in self.manifest.files.items():
            if path not in names:
                raise TscanError(f"{path} listed in the manifest but missing")
            digest, size = hashlib.sha256(), 0
            try:
                with self._zip.open(path) as f:
                    for chunk in iter(lambda f=f: f.read(1 << 20), b""):
                        digest.update(chunk)
                        size += len(chunk)
            except zipfile.BadZipFile as exc:  # the ZIP's own CRC caught it first
                raise TscanError(f"{path}: checksum mismatch (file damaged: {exc})") from exc
            if size != info.size or digest.hexdigest() != info.sha256:
                raise TscanError(f"{path}: checksum mismatch (file damaged)")

    # ------------------------------------------------------------------ frames
    def _discarded(self, t: float, segment: int) -> bool:
        seg = self.manifest.segments[segment]
        return any(t0 <= t <= t1 for t0, t1 in seg.discarded)

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    def kept_mask(self) -> NDArray[np.bool_]:
        return np.array(
            [not self._discarded(float(r["t"]), int(r["segment"])) for r in self._frames],
            dtype=bool,
        )

    def poses(self, include_discarded: bool = False) -> tuple[Floats, Floats]:
        """(t, Nx4x4 poses in the RaceForge frame) — fast path without depth."""
        rows = self._frames if include_discarded else self._frames[self.kept_mask()]
        ar = rows["pose"].astype(np.float64).reshape(-1, 4, 4).transpose(0, 2, 1)  # column-major
        return rows["t"].astype(np.float64), np.einsum("ij,njk->nik", ARKIT_TO_RF, ar)

    def frames(self, include_discarded: bool = False, with_depth: bool = True) -> Iterator[Frame]:
        d = self.manifest.depth
        n = d.width * d.height
        streams: dict[int, IO[bytes]] = {}
        try:
            for i, r in enumerate(self._frames):
                seg = int(r["segment"])
                depth = conf = None
                if r["has_depth"] and with_depth:
                    if seg not in streams:
                        streams[seg] = self._zip.open(self.manifest.segments[seg].depth)
                    depth, conf = _read_depth(streams[seg], n, d.height, d.width)
                elif r["has_depth"] and seg in streams:
                    _skip_depth(streams[seg])
                t = float(r["t"])
                if not include_discarded and self._discarded(t, seg):
                    continue
                pose_ar = r["pose"].astype(np.float64).reshape(4, 4).T
                yield Frame(
                    index=i,
                    t=t,
                    segment=seg,
                    tracking=TRACKING[int(r["tracking"])] if r["tracking"] < 3 else "unknown",
                    pose=arkit_to_rf(pose_ar),
                    pose_arkit=pose_ar,
                    intrinsics=r["intrinsics"].astype(np.float64).reshape(3, 3).T,
                    exposure_s=float(r["exposure_s"]),
                    depth=depth,
                    confidence=conf,
                )
        finally:
            for s in streams.values():
                s.close()

    def imu(self) -> NDArray[Any]:
        return np.frombuffer(self._zip.read("imu.bin"), dtype=IMU_DTYPE)

    # ------------------------------------------------------------------ geometry & extras
    def mesh(self, segment: int) -> Mesh | None:
        path = self.manifest.segments[segment].mesh
        return None if path is None else read_ply(self._zip.read(path))

    def roomplan(self) -> dict[str, Any] | None:
        if not self.manifest.roomplan:
            return None
        return json.loads(self._zip.read("roomplan.json"))

    def world_map(self) -> bytes | None:
        return self._zip.read("worldmap.arworldmap") if self.manifest.world_map.included else None

    def summary(self) -> dict[str, Any]:
        m = self.manifest
        kept = self.kept_mask()
        recorded = sum(s.end_s - s.start_s for s in m.segments)
        discarded = sum(
            max(0.0, min(t1, s.end_s) - t0) for s in m.segments for t0, t1 in s.discarded
        )
        return {
            "project": m.project.name,
            "pass": m.pass_.type,
            "quality": m.quality,
            "created_at": m.created_at.isoformat(),
            "device": f"{m.device.model} ({m.device.system})",
            "segments": len(m.segments),
            "frames": self.frame_count,
            "frames_kept": int(kept.sum()),
            "depth_frames": int(self._frames["has_depth"].sum()),
            "duration_s": round(recorded, 2),
            "kept_s": round(recorded - discarded, 2),
            "aligned": m.world_map.aligned,
            "world_map": m.world_map.included,
            "roomplan": m.roomplan,
            "meshes": sum(1 for s in m.segments if s.mesh),
            "videos": sum(1 for s in m.segments if s.video),
        }


def _read_depth(
    stream: IO[bytes], n: int, h: int, w: int
) -> tuple[NDArray[np.float32], NDArray[np.uint8]]:
    head = stream.read(4)
    if len(head) != 4:
        raise TscanError("depth stream ended early")
    payload = stream.read(int.from_bytes(head, "little"))
    try:
        raw = zlib.decompress(payload, -15)
    except zlib.error as exc:
        raise TscanError(f"depth record damaged: {exc}") from exc
    if len(raw) != n * 3:
        raise TscanError("depth record has the wrong size")
    depth = np.frombuffer(raw[: 2 * n], dtype="<f2").astype(np.float32).reshape(h, w)
    conf = np.frombuffer(raw[2 * n :], dtype=np.uint8).reshape(h, w)
    return depth, conf


def _skip_depth(stream: IO[bytes]) -> None:
    stream.read(int.from_bytes(stream.read(4), "little"))


def read_ply(data: bytes) -> Mesh:
    """Binary little-endian PLY as written by TrackScout (triangles + per-face classification)."""
    end = data.find(b"end_header\n")
    if not data.startswith(b"ply\n") or end < 0:
        raise TscanError("not a PLY mesh")
    header = data[:end].decode("ascii").splitlines()
    if "format binary_little_endian 1.0" not in header:
        raise TscanError("PLY must be binary little-endian")
    counts = {parts[1]: int(parts[2]) for line in header if (parts := line.split())[0] == "element"}
    nv, nf = counts.get("vertex", 0), counts.get("face", 0)
    body = data[end + len(b"end_header\n") :]
    verts = np.frombuffer(body, dtype="<f4", count=nv * 3).reshape(nv, 3).astype(np.float64)
    face_dt = np.dtype([("n", "u1"), ("idx", "<u4", (3,)), ("cls", "u1")])
    faces = np.frombuffer(body, dtype=face_dt, count=nf, offset=nv * 12)
    if nf and not np.all(faces["n"] == 3):
        raise TscanError("only triangle meshes are supported")
    rf = verts @ ARKIT_TO_RF[:3, :3].T
    return Mesh(vertices=rf, faces=faces["idx"].copy(), classification=faces["cls"].copy())
