"""Spec 0006 AC7: two clients, offline edits, reconnect → both versions kept as branches and the
conflict is shown; blobs are fetched lazily and cached."""

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from raceforge.backend.security import totp_now
from raceforge.construct.quickstart import QuickStartParams, generate
from raceforge.core import io
from raceforge.parts.catalogue import Catalogue
from raceforge.workspace.client import OfflineError
from raceforge.workspace.sync import Workspace
from tests.backend.conftest import ADMIN_PW, MEMBER_PW, Env, Team


class Net:
    def __init__(self) -> None:
        self.online = True
        self.requests = 0


def factory(app: FastAPI, net: Net):
    class Gated(TestClient):
        def send(self, *args: Any, **kwargs: Any) -> httpx.Response:  # type: ignore[override]
            if not net.online:
                raise httpx.ConnectError("offline")
            net.requests += 1
            return super().send(*args, **kwargs)

    return lambda _url: Gated(app)


def _car(cat: Catalogue, wheelbase: int) -> dict[str, Any]:
    return io.to_jsonable(generate(QuickStartParams(wheelbase_studs=wheelbase), cat).assembly)


@pytest.fixture
def clients(env: Env, team: Team, tmp_path: Path) -> tuple[Workspace, Workspace, Net, Net]:
    na, nb = Net(), Net()
    a = Workspace(tmp_path / "a", factory(env.client.app, na))
    b = Workspace(tmp_path / "b", factory(env.client.app, nb))
    a.login("http://backend", "anna", MEMBER_PW, None)
    b.login("http://backend", "admin", ADMIN_PW, totp_now(team.admin_totp, env.clock()))
    for w in (a, b):
        assert [x.id for x in w.workspaces()] == [team.ws]
        w.select(team.ws)
    return a, b, na, nb


def test_offline_edits_become_branches(
    clients: tuple[Workspace, Workspace, Net, Net], cat: Catalogue, env: Env
) -> None:
    a, b, na, nb = clients
    v1 = a.save("assembly", "car-a", _car(cat, 11), "first")
    assert not v1.pending and v1.semver == "1.0.0"
    assert b.sync().pulled == 1
    [obj] = b.objects()
    assert obj.slug == "car-a" and obj.latest and obj.latest.id == v1.id
    assert io.load(b.version_content(v1.id))  # content fetched on demand
    # both go offline and edit the same car
    na.online = nb.online = False
    env.clock.advance(seconds=1)
    va = a.save("assembly", "car-a", _car(cat, 13), "longer")
    env.clock.advance(seconds=1)
    vb = b.save("assembly", "car-a", _car(cat, 15), "even longer")
    assert va.pending and vb.pending and va.parents == vb.parents == [v1.id]
    assert not a.status().online and a.status().pending == 1
    with pytest.raises(OfflineError):
        a.sync()
    # reconnect
    na.online = nb.online = True
    assert a.sync().pushed == 1
    rb = b.sync()
    assert rb.pushed == 1 and rb.pulled == 1
    [conflict] = rb.conflicts
    assert conflict.parent == v1.id and {v.id for v in conflict.versions} == {va.id, vb.id}
    assert next(v for v in b.history(obj.id) if v.id == vb.id).branch  # nothing overwritten
    ra = a.sync()
    assert ra.pulled == 1 and len(ra.conflicts) == 1
    assert {v.semver for v in a.history(obj.id)} == {"1.0.0", "1.0.1", "1.0.2"}
    assert a.status().conflicts == 1


def test_blobs_lazy_and_cached(
    clients: tuple[Workspace, Workspace, Net, Net], tmp_path: Path
) -> None:
    a, b, _na, nb = clients
    ctrl = tmp_path / "my_ctrl.py"
    ctrl.write_bytes(b"# controller\n" * 500)  # > one 1 KiB part: chunked upload
    params = tmp_path / "my_ctrl.yaml"
    params.write_text("gain: 1.0\n")
    v = a.save_files("controller", "my-ctrl", [ctrl, params], "first controller")
    assert not v.pending
    b.sync()
    content = b.version_content(v.id)
    sha = content["files"][0]["sha256"]
    assert not b.blob_path(sha).exists()  # not fetched until needed
    path = b.blob(sha)
    assert path.read_bytes() == ctrl.read_bytes()
    nb.online = False
    assert b.blob(sha) == path  # cached: works offline


def test_resume_partial_download(
    clients: tuple[Workspace, Workspace, Net, Net], tmp_path: Path
) -> None:
    a, b, _na, _nb = clients
    data = bytes(range(256)) * 20
    f = tmp_path / "scan.bin"
    f.write_bytes(data)
    v = a.save_files("capture", "scan-1", [f])
    b.sync()
    sha = b.version_content(v.id)["files"][0]["sha256"]
    dest = b.blob_path(sha)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.with_name(dest.name + ".part").write_bytes(data[:1000])  # interrupted download
    assert b.blob(sha).read_bytes() == data
