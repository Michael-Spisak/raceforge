"""Python twin of TrackScoutKit's `Synthetic.makePass` (spec 0007 AC3): the same pass, written with
`zipfile`, so the importer tests also run where Swift is not available. CI checks both writers
against the same assertions (tests/capture/test_tscan.py)."""

import hashlib
import json
import struct
import zipfile
import zlib
from pathlib import Path

import numpy as np

W, H = 16, 12
INTRINSICS = [210, 0, 0, 0, 210, 0, 8, 6, 1]


def pose(i: int) -> list[float]:
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0.1 * i, 1.4, -0.05 * i, 1]


def depth_value(i: int) -> float:
    return 1.5 + i * 0.25


def _ply() -> bytes:
    verts = [(-1, 0, 1), (3, 0, 1), (3, 0, -2), (-1, 0, -2), (-1, 2, -2)]
    faces = [((0, 1, 2), 2), ((0, 2, 3), 2), ((3, 2, 4), 1)]
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        "comment TrackScout mesh, ARKit world frame (Y-up, metres)\n"
        f"element vertex {len(verts)}\nproperty float x\nproperty float y\nproperty float z\n"
        f"element face {len(faces)}\nproperty list uchar uint vertex_indices\n"
        "property uchar classification\nend_header\n"
    ).encode()
    body = b"".join(struct.pack("<3f", *v) for v in verts)
    body += b"".join(struct.pack("<B3IB", 3, *f, c) for f, c in faces)
    return header + body


def make_pass(path: Path) -> Path:
    frames, imu, depth = bytearray(), bytearray(), {0: bytearray(), 1: bytearray()}
    times = [k * 0.1 for k in range(10)] + [2.0 + k * 0.1 for k in range(10)]
    for i, t in enumerate(times):
        seg = 0 if i < 10 else 1
        frames += struct.pack("<dHBB16f9ff", t, seg, 2, 1, *pose(i), *INTRINSICS, 0.01)
        raw = np.full(W * H, depth_value(i), dtype="<f2").tobytes() + bytes([2]) * (W * H)
        c = zlib.compressobj(wbits=-15)
        packed = c.compress(raw) + c.flush()
        depth[seg] += struct.pack("<I", len(packed)) + packed
        imu += struct.pack("<d9f", t, 0, -1, 0, 0, 0, 0, 0, 0, 0.1)
    files = {
        "frames.bin": bytes(frames),
        "imu.bin": bytes(imu),
        "depth/0.bin": bytes(depth[0]),
        "depth/1.bin": bytes(depth[1]),
        "mesh/0.ply": _ply(),
        "worldmap.arworldmap": b"synthetic world map",
    }
    manifest = {
        "schema": "tscan",
        "schema_version": 1,
        "app": {"name": "TrackScout", "version": "0.1.0"},
        "device": {"model": "synthetic", "system": "test"},
        "project": {"id": "proj-1", "name": "Corridor Test"},
        "pass": {
            "id": "pass-1",
            "type": "walkthrough",
            "conditions": {"lights": "on", "doors": "closed", "note": "synthetic"},
        },
        "quality": "high",
        "created_at": "2026-10-03T13:20:00Z",
        "coordinate_frame": "arkit",
        "world_map": {"id": "map-1", "included": True, "aligned": True, "aligned_at_s": 0.4},
        "depth": {"width": W, "height": H, "every_nth_frame": 1},
        "segments": [
            {
                "index": 0,
                "start_s": 0,
                "end_s": 1.0,
                "frames": 10,
                "depth": "depth/0.bin",
                "mesh": "mesh/0.ply",
                "discarded": [],
            },
            {
                "index": 1,
                "start_s": 2.0,
                "end_s": 2.9,
                "frames": 10,
                "depth": "depth/1.bin",
                "discarded": [[2.9 - 0.3, 2.9]],
            },
        ],
        "roomplan": False,
        "files": {
            k: {"sha256": hashlib.sha256(v).hexdigest(), "size": len(v)} for k, v in files.items()
        },
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=1, sort_keys=True))
        for k, v in sorted(files.items()):
            z.writestr(k, v)
    return path
