"""Spec 0006 AC2: invite → register → login → refresh → logout, TOTP, scopes, rate limit."""

from raceforge.backend.security import totp_now
from tests.backend.conftest import ADMIN_PW, MEMBER_PW, Env, Team


def test_invite_register_login_refresh_logout(env: Env, team: Team) -> None:
    c = env.client
    r = c.post("/api/v1/auth/login", json={"username": "anna", "password": MEMBER_PW})
    pair = r.json()
    assert r.status_code == 200 and pair["user"]["role"] == "member"
    assert "rf_access" in r.cookies  # browser path: HTTP-only cookie
    c.cookies.clear()
    h = {"Authorization": f"Bearer {pair['access_token']}"}
    assert c.get("/api/v1/users/me", headers=h).json()["username"] == "anna"
    r2 = c.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r2.status_code == 200
    c.cookies.clear()
    # rotation: the old access token and the old refresh token are dead
    assert c.get("/api/v1/users/me", headers=h).status_code == 401
    reuse = c.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert reuse.status_code == 401
    # reuse revoked the whole session, including the rotated pair
    h2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    assert c.get("/api/v1/users/me", headers=h2).status_code == 401
    h3 = env.login("anna", MEMBER_PW)
    assert c.post("/api/v1/auth/logout", headers=h3).status_code == 204
    assert c.get("/api/v1/users/me", headers=h3).status_code == 401


def test_access_token_expires(env: Env, team: Team) -> None:
    env.clock.advance(minutes=16)
    assert env.client.get("/api/v1/users/me", headers=team.member).status_code == 401


def test_invite_single_use(env: Env, team: Team) -> None:
    inv = env.client.post("/api/v1/invites", headers=team.admin, json={"role": "member"}).json()
    assert inv["link"].endswith(inv["token"])
    body = {
        "invite_token": inv["token"],
        "username": "ben",
        "display_name": "Ben",
        "password": "ben-password-1",
    }
    assert env.client.post("/api/v1/auth/register", json=body).status_code == 201
    again = env.client.post("/api/v1/auth/register", json={**body, "username": "ben2"})
    assert again.status_code == 400
    assert env.client.get("/api/v1/invites", headers=team.member).status_code == 403


def test_admin_without_totp_cannot_use_admin_endpoints(env: Env) -> None:
    env.backend.bootstrap_admin("boss", ADMIN_PW)
    h = env.login("boss", ADMIN_PW)
    r = env.client.get("/api/v1/audit", headers=h)
    assert r.status_code == 403 and "TOTP" in r.json()["detail"]


def test_totp_login(env: Env, team: Team) -> None:
    c = env.client
    r = c.post("/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert r.status_code == 401 and r.json()["detail"] == "totp_required"
    wrong = c.post(
        "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PW, "totp": "000000"}
    )
    if totp_now(team.admin_totp, env.clock()) != "000000":
        assert wrong.status_code == 401
    h = env.login("admin", ADMIN_PW, team.admin_totp)
    assert c.get("/api/v1/audit", headers=h).status_code == 200


def test_token_scopes(env: Env, team: Team) -> None:
    c = env.client
    obj = c.post(
        f"/api/v1/workspaces/{team.ws}/objects",
        headers=team.member,
        json={"kind": "assembly", "slug": "car-a"},
    ).json()
    read = c.post(
        "/api/v1/tokens", headers=team.member, json={"name": "laptop", "scopes": ["read"]}
    ).json()
    rh = {"Authorization": f"Bearer {read['token']}"}
    assert c.get(f"/api/v1/workspaces/{team.ws}/objects", headers=rh).status_code == 200
    r = c.post(f"/api/v1/objects/{obj['id']}/versions", headers=rh, json={"content": {}})
    assert r.status_code == 403 and "edit" in r.json()["detail"]
    # members cannot mint admin tokens; nobody can give MCP the admin scope
    assert (
        c.post(
            "/api/v1/tokens", headers=team.member, json={"name": "x", "scopes": ["admin"]}
        ).status_code
        == 403
    )
    mcp = c.post(
        "/api/v1/tokens",
        headers=team.admin,
        json={"name": "claude", "scopes": ["read", "admin"], "client": "mcp"},
    )
    assert mcp.status_code == 422
    # API tokens cannot create further tokens
    assert (
        c.post("/api/v1/tokens", headers=rh, json={"name": "y", "scopes": ["read"]}).status_code
        == 403
    )
    # revoke
    assert c.delete(f"/api/v1/tokens/{read['id']}", headers=team.member).status_code == 204
    assert c.get("/api/v1/users/me", headers=rh).status_code == 401
    listed = c.get("/api/v1/tokens", headers=team.admin).json()
    assert any(t["user"] == "anna" and t["revoked"] for t in listed)  # admin sees all tokens


def test_token_expiry(env: Env, team: Team) -> None:
    exp = (env.clock().replace(microsecond=0)).isoformat().replace("+00:00", "Z")
    r = env.client.post(
        "/api/v1/tokens",
        headers=team.member,
        json={"name": "old", "scopes": ["read"], "expires_at": exp},
    )
    assert r.status_code == 422


def test_login_rate_limit(env: Env, team: Team) -> None:
    c = env.client
    for _ in range(5):
        r = c.post("/api/v1/auth/login", json={"username": "anna", "password": "wrong-password"})
        assert r.status_code == 401
    r = c.post("/api/v1/auth/login", json={"username": "anna", "password": MEMBER_PW})
    assert r.status_code == 429
    env.clock.advance(minutes=6)
    r = c.post("/api/v1/auth/login", json={"username": "anna", "password": MEMBER_PW})
    assert r.status_code == 200
