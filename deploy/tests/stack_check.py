"""Helper for deploy/tests/stack-e2e.sh (spec 0006 AC1/AC8/AC9) — talks to a running Compose stack.

- ``seed URL OUT.json``: create a workspace, versions and blobs; write a fingerprint
- ``verify URL IN.json``: compare the stack's content with a fingerprint
- ``hammer URL SECONDS``: send requests continuously; exit 1 if any fails
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ADMIN, PASSWORD = "admin", os.environ.get("RF_ADMIN_PASSWORD", "e2e-admin-password")


def login(c: httpx.Client) -> None:
    r = c.post("/api/v1/auth/login", json={"username": ADMIN, "password": PASSWORD})
    r.raise_for_status()
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    c.cookies.clear()


def fingerprint(c: httpx.Client) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ws in c.get("/api/v1/workspaces").json():
        for obj in c.get(f"/api/v1/workspaces/{ws['id']}/objects").json():
            versions = c.get(f"/api/v1/objects/{obj['id']}/versions").json()
            out[f"{ws['name']}/{obj['slug']}"] = [
                (v["id"], v["semver"], v["content_hash"]) for v in versions
            ]
            for v in versions:
                content = c.get(f"/api/v1/versions/{v['id']}").json()["content"]
                for f in content.get("files", []):
                    data = c.get(f"/api/v1/blobs/{f['sha256']}").content
                    out[f"blob:{f['sha256']}"] = hashlib.sha256(data).hexdigest()
    return out


def seed(c: httpx.Client) -> None:
    ws = c.post("/api/v1/workspaces", json={"name": "E2E"}).json()
    obj = c.post(
        f"/api/v1/workspaces/{ws['id']}/objects", json={"kind": "controller", "slug": "ctrl"}
    ).json()
    for i in range(2):
        data = os.urandom(3 * 1024 * 1024 + i)
        sha = hashlib.sha256(data).hexdigest()
        up = c.post("/api/v1/uploads", json={"sha256": sha, "size": len(data)}).json()
        for n in range(up["parts"]):
            part = data[n * up["part_size"] : (n + 1) * up["part_size"]]
            r = c.put(
                f"/api/v1/uploads/{up['id']}/parts/{n}",
                content=part,
                headers={"X-Part-SHA256": hashlib.sha256(part).hexdigest()},
            )
            r.raise_for_status()
        c.post(f"/api/v1/uploads/{up['id']}/complete").raise_for_status()
        content = {
            "schema": "fileset",
            "schema_version": 1,
            "files": [{"path": "ctrl.py", "sha256": sha, "size": len(data)}],
        }
        c.post(
            f"/api/v1/objects/{obj['id']}/versions", json={"content": content, "message": f"v{i}"}
        ).raise_for_status()


def hammer(c: httpx.Client, seconds: float) -> int:
    ok = failed = 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            r = c.get("/api/v1/workspaces")
            if r.status_code == 200:
                ok += 1
            else:
                failed += 1
                print(f"HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
        except httpx.HTTPError as exc:
            failed += 1
            print(f"error: {exc}", file=sys.stderr)
        time.sleep(0.02)
    print(f"hammer: {ok} ok, {failed} failed")
    return 1 if failed or ok == 0 else 0


def main() -> int:
    cmd, url = sys.argv[1], sys.argv[2]
    with httpx.Client(base_url=url, timeout=30) as c:
        login(c)
        if cmd == "seed":
            seed(c)
            Path(sys.argv[3]).write_text(json.dumps(fingerprint(c), indent=1, sort_keys=True))
            return 0
        if cmd == "verify":
            want = json.loads(Path(sys.argv[3]).read_text())
            got = json.loads(json.dumps(fingerprint(c)))
            if got != want:
                print(f"MISMATCH\nwant {want}\ngot  {got}", file=sys.stderr)
                return 1
            print(f"verified {len(want)} entries")
            return 0
        if cmd == "hammer":
            return hammer(c, float(sys.argv[3]))
    return 2


if __name__ == "__main__":
    sys.exit(main())
