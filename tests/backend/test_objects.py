"""Spec 0006 AC3, AC5, AC6: immutable versions, drafts, trash, audit, workspaces."""

import hashlib
from typing import Any

import pytest

from raceforge.construct.quickstart import QuickStartParams, generate
from raceforge.core import io
from raceforge.parts.catalogue import Catalogue
from tests.backend.conftest import Env, Team


@pytest.fixture(scope="module")
def assembly(cat: Catalogue) -> dict[str, Any]:
    return io.to_jsonable(generate(QuickStartParams(), cat).assembly)


@pytest.fixture(scope="module")
def assembly2(cat: Catalogue) -> dict[str, Any]:
    return io.to_jsonable(generate(QuickStartParams(wheelbase_studs=13), cat).assembly)


def _obj(env: Env, team: Team, slug: str = "car-a", kind: str = "assembly") -> str:
    r = env.client.post(
        f"/api/v1/workspaces/{team.ws}/objects",
        headers=team.member,
        json={"kind": kind, "slug": slug, "tags": ["race"]},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_versions_immutable_and_hashed(
    env: Env, team: Team, assembly: dict[str, Any], assembly2: dict[str, Any]
) -> None:
    c, h = env.client, team.member
    oid = _obj(env, team)
    v1 = c.post(
        f"/api/v1/objects/{oid}/versions",
        headers=h,
        json={"content": assembly, "message": "first", "name": "baseline"},
    ).json()
    model = io.load(assembly)
    assert v1["content_hash"] == io.content_hash(model)
    assert v1["semver"] == "1.0.0" and v1["parents"] == [] and v1["author"] == "anna"
    env.clock.advance(seconds=1)
    v2 = c.post(f"/api/v1/objects/{oid}/versions", headers=h, json={"content": assembly2}).json()
    assert v2["semver"] == "1.0.1" and v2["parents"] == [v1["id"]] and not v2["branch"]
    got = c.get(f"/api/v1/versions/{v1['id']}", headers=h).json()
    assert io.content_hash(io.load(got["content"])) == v1["content_hash"]
    # no endpoint changes a version; same id with other content is refused, same content is a no-op
    assert c.put(f"/api/v1/versions/{v1['id']}", headers=h, json={}).status_code == 405
    assert (
        c.post(
            f"/api/v1/objects/{oid}/versions",
            headers=h,
            json={"id": v1["id"], "content": assembly2},
        ).status_code
        == 409
    )
    same = c.post(
        f"/api/v1/objects/{oid}/versions", headers=h, json={"id": v1["id"], "content": assembly}
    )
    assert same.json()["id"] == v1["id"]
    assert (
        c.post(
            f"/api/v1/objects/{oid}/versions",
            headers=h,
            json={"content": assembly, "semver": "1.0.0"},
        ).status_code
        == 409
    )
    # branch: parent is not the latest
    env.clock.advance(seconds=1)
    b = c.post(
        f"/api/v1/objects/{oid}/versions",
        headers=h,
        json={"content": assembly2, "parents": [v1["id"]]},
    ).json()
    assert b["branch"] and b["semver"] == "1.0.2"
    listed = c.get(f"/api/v1/objects/{oid}/versions", headers=h).json()
    assert [v["semver"] for v in listed] == ["1.0.0", "1.0.1", "1.0.2"]
    objs = c.get(
        f"/api/v1/workspaces/{team.ws}/objects",
        headers=h,
        params={"kind": "assembly", "tag": "race"},
    ).json()
    assert objs[0]["latest"]["id"] == b["id"]


def test_invalid_content_rejected(env: Env, team: Team, assembly: dict[str, Any]) -> None:
    oid = _obj(env, team)
    bad = {**assembly, "root": "missing"}
    r = env.client.post(
        f"/api/v1/objects/{oid}/versions", headers=team.member, json={"content": bad}
    )
    assert r.status_code == 422 and "root submodel 'missing' not defined" in r.json()["detail"]
    track = env.client.post(
        f"/api/v1/objects/{oid}/versions",
        headers=team.member,
        json={"content": {"schema": "track", "schema_version": 1}},
    )
    assert track.status_code == 422 and "assembly" in track.json()["detail"]


def test_fileset_for_controllers(env: Env, team: Team) -> None:
    oid = _obj(env, team, "wall-follow", "controller")
    data = b"class C: pass\n"
    sha = hashlib.sha256(data).hexdigest()
    content = {
        "schema": "fileset",
        "schema_version": 1,
        "entry": "ctrl.py",
        "files": [{"path": "ctrl.py", "sha256": sha, "size": len(data)}],
    }
    r = env.client.post(
        f"/api/v1/objects/{oid}/versions", headers=team.member, json={"content": content}
    )
    assert r.status_code == 422 and "not uploaded" in r.json()["detail"]
    up = env.client.post(
        "/api/v1/uploads", headers=team.member, json={"sha256": sha, "size": len(data)}
    ).json()
    env.client.put(
        f"/api/v1/uploads/{up['id']}/parts/0",
        headers={**team.member, "X-Part-SHA256": sha},
        content=data,
    )
    env.client.post(f"/api/v1/uploads/{up['id']}/complete", headers=team.member)
    r = env.client.post(
        f"/api/v1/objects/{oid}/versions", headers=team.member, json={"content": content}
    )
    assert r.status_code == 201, r.text


def test_drafts_per_user(env: Env, team: Team, assembly: dict[str, Any]) -> None:
    c = env.client
    oid = _obj(env, team)
    assert (
        c.put(
            f"/api/v1/objects/{oid}/draft", headers=team.member, json={"content": {"wip": True}}
        ).status_code
        == 200
    )
    assert c.get(f"/api/v1/objects/{oid}/draft", headers=team.member).json()["content"] == {
        "wip": True
    }
    assert c.get(f"/api/v1/objects/{oid}/draft", headers=team.admin).status_code == 404
    assert c.get(f"/api/v1/objects/{oid}/versions", headers=team.member).json() == []


def test_trash_restore_purge(env: Env, team: Team, assembly: dict[str, Any]) -> None:
    c = env.client
    oid = _obj(env, team)
    c.post(f"/api/v1/objects/{oid}/versions", headers=team.member, json={"content": assembly})
    assert c.delete(f"/api/v1/objects/{oid}", headers=team.member).status_code == 403
    assert c.delete(f"/api/v1/objects/{oid}", headers=team.admin).status_code == 204
    assert c.get(f"/api/v1/objects/{oid}", headers=team.member).status_code == 404
    assert c.get(f"/api/v1/workspaces/{team.ws}/objects", headers=team.member).json() == []
    assert [o["id"] for o in c.get("/api/v1/trash", headers=team.admin).json()] == [oid]
    assert c.post(f"/api/v1/objects/{oid}/restore", headers=team.admin).status_code == 200
    assert c.get(f"/api/v1/objects/{oid}", headers=team.member).status_code == 200
    # time travel: purge only after 30 days
    c.delete(f"/api/v1/objects/{oid}", headers=team.admin)
    env.clock.advance(days=29)
    assert env.backend.purge_trash() == 0
    env.clock.advance(days=2)
    assert env.backend.purge_trash() == 1
    admin = env.login("admin", "admin-password-1", team.admin_totp)  # old session expired
    assert c.get("/api/v1/trash", headers=admin).json() == []


def test_audit_records_every_write(env: Env, team: Team, assembly: dict[str, Any]) -> None:
    c = env.client
    tok = c.post(
        "/api/v1/tokens",
        headers=team.member,
        json={"name": "cli", "scopes": ["read", "edit"], "client": "cli"},
    ).json()
    th = {"Authorization": f"Bearer {tok['token']}"}
    oid = c.post(
        f"/api/v1/workspaces/{team.ws}/objects",
        headers=th,
        json={"kind": "assembly", "slug": "car-b"},
    ).json()["id"]
    c.post(f"/api/v1/objects/{oid}/versions", headers=th, json={"content": assembly})
    entries = c.get("/api/v1/audit", headers=team.admin).json()
    by_action = {e["action"]: e for e in entries}
    for action in (
        "object.create",
        "version.create",
        "token.create",
        "workspace.create",
        "user.register",
        "invite.create",
        "auth.login",
    ):
        assert action in by_action, action
    v = by_action["version.create"]
    assert v["user"] == "anna" and v["token_id"] == tok["id"] and v["client"] == "cli"


def test_workspaces_and_copy(env: Env, team: Team, assembly: dict[str, Any]) -> None:
    c, h = env.client, team.member
    oid = _obj(env, team)
    v = c.post(f"/api/v1/objects/{oid}/versions", headers=h, json={"content": assembly}).json()
    race = c.post("/api/v1/workspaces", headers=h, json={"name": "Race"}).json()
    assert c.post("/api/v1/workspaces", headers=h, json={"name": "Race"}).status_code == 409
    copy = c.post(
        f"/api/v1/objects/{oid}/copy", headers=h, json={"workspace_id": race["id"]}
    ).json()
    assert copy["copied_from"] == v["id"] and copy["latest"]["content_hash"] == v["content_hash"]
    r = c.patch(f"/api/v1/workspaces/{race['id']}", headers=h, json={"name": "Race day"})
    assert r.json()["name"] == "Race day"
    names = [w["name"] for w in c.get("/api/v1/workspaces", headers=team.admin).json()]
    assert names == ["Race day", "Season 1"]  # every member sees every workspace
    assert (
        c.post(
            f"/api/v1/workspaces/{team.ws}/objects",
            headers=h,
            json={"kind": "assembly", "slug": "car-a"},
        ).status_code
        == 409
    )


def test_status(env: Env) -> None:
    st = env.client.get("/api/v1/status").json()
    assert st["database"] and st["blobs"] and st["disk_level"] in ("ok", "warn", "high", "critical")
