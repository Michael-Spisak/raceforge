"""Spec 0006 AC4: chunked resumable uploads, dedupe, Range downloads, checksums."""

import hashlib
import os

from tests.backend.conftest import Env, Team

PART = 1024  # max_part_bytes in the test settings


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _put(env: Env, team: Team, up: str, n: int, data: bytes, sha: str | None = None) -> int:
    return env.client.put(
        f"/api/v1/uploads/{up}/parts/{n}",
        headers={**team.member, "X-Part-SHA256": sha or _sha(data)},
        content=data,
    ).status_code


def test_resumable_upload_dedupe_range(env: Env, team: Team) -> None:
    c, h = env.client, team.member
    data = os.urandom(PART * 3 + 100)
    sha = _sha(data)
    parts = [data[i : i + PART] for i in range(0, len(data), PART)]
    up = c.post("/api/v1/uploads", headers=h, json={"sha256": sha, "size": len(data)}).json()
    assert up["status"] == "open" and up["parts"] == 4
    assert _put(env, team, up["id"], 0, parts[0]) == 200
    assert _put(env, team, up["id"], 2, parts[2]) == 200
    # "disconnect": the client starts over and learns which parts already arrived
    again = c.post("/api/v1/uploads", headers=h, json={"sha256": sha, "size": len(data)}).json()
    assert again["id"] == up["id"] and again["received"] == [0, 2]
    assert c.post(f"/api/v1/uploads/{up['id']}/complete", headers=h).status_code == 409
    for n in (1, 3):
        assert _put(env, team, up["id"], n, parts[n]) == 200
    done = c.post(f"/api/v1/uploads/{up['id']}/complete", headers=h).json()
    assert done["status"] == "complete"
    # duplicate upload is skipped
    dup = c.post("/api/v1/uploads", headers=h, json={"sha256": sha, "size": len(data)}).json()
    assert dup["status"] == "exists" and dup["id"] is None
    # downloads
    assert c.head(f"/api/v1/blobs/{sha}", headers=h).headers["content-length"] == str(len(data))
    assert c.get(f"/api/v1/blobs/{sha}", headers=h).content == data
    r = c.get(f"/api/v1/blobs/{sha}", headers={**h, "Range": "bytes=1000-2099"})
    assert r.status_code == 206 and r.content == data[1000:2100]
    assert r.headers["content-range"] == f"bytes 1000-2099/{len(data)}"
    assert c.get(f"/api/v1/blobs/{sha}", headers={**h, "Range": "bytes=-10"}).content == data[-10:]
    assert (
        c.get(f"/api/v1/blobs/{sha}", headers={**h, "Range": "bytes=3000-"}).content == data[3000:]
    )
    bad = c.get(f"/api/v1/blobs/{sha}", headers={**h, "Range": f"bytes={len(data)}-"})
    assert bad.status_code == 416
    assert c.get(f"/api/v1/blobs/{'0' * 64}", headers=h).status_code == 404


def test_corrupted_part_detected(env: Env, team: Team) -> None:
    data = os.urandom(PART + 10)
    up = env.client.post(
        "/api/v1/uploads", headers=team.member, json={"sha256": _sha(data), "size": len(data)}
    ).json()
    corrupted = bytearray(data[:PART])
    corrupted[5] ^= 0xFF
    assert _put(env, team, up["id"], 0, bytes(corrupted), _sha(data[:PART])) == 400
    assert _put(env, team, up["id"], 0, data[:PART][:-1]) == 400  # wrong size
    assert (
        env.client.get(f"/api/v1/uploads/{up['id']}", headers=team.member).json()["received"] == []
    )


def test_wrong_file_detected_on_complete(env: Env, team: Team) -> None:
    data, other = os.urandom(100), os.urandom(100)
    up = env.client.post(
        "/api/v1/uploads", headers=team.member, json={"sha256": _sha(data), "size": 100}
    ).json()
    assert _put(env, team, up["id"], 0, other) == 200  # part checksum fine, wrong file
    r = env.client.post(f"/api/v1/uploads/{up['id']}/complete", headers=team.member)
    assert r.status_code == 400


def test_uploads_are_private(env: Env, team: Team) -> None:
    data = b"hello"
    up = env.client.post(
        "/api/v1/uploads", headers=team.member, json={"sha256": _sha(data), "size": 5}
    ).json()
    r = env.client.put(
        f"/api/v1/uploads/{up['id']}/parts/0",
        headers={**team.admin, "X-Part-SHA256": _sha(data)},
        content=data,
    )
    assert r.status_code == 404


def test_export_import_blobs(env: Env, team: Team, tmp_path) -> None:
    """Backup helpers (AC8 code part): export is incremental, import restores verified bytes."""
    data = os.urandom(3000)
    sha = _sha(data)
    up = env.client.post(
        "/api/v1/uploads", headers=team.member, json={"sha256": sha, "size": len(data)}
    ).json()
    for n in range(up["parts"]):
        assert _put(env, team, up["id"], n, data[n * PART : (n + 1) * PART]) == 200
    env.client.post(f"/api/v1/uploads/{up['id']}/complete", headers=team.member)
    backup = tmp_path / "backup"
    assert env.backend.export_blobs(backup) == 1
    assert env.backend.export_blobs(backup) == 0
    from raceforge.backend.blobs import blob_key

    env.backend.store.delete(blob_key(sha))  # "fresh" store after a disaster
    assert env.backend.import_blobs(backup) == 1
    assert env.client.get(f"/api/v1/blobs/{sha}", headers=team.member).content == data
