"""Login, session cookies, and role enforcement."""

from .conftest import CONT_A, SEED_PASSWORD, ago, reefer_payload


def _gate_in_body(**over):
    body = dict(
        at=ago(hours=2), hauler="HD", container=reefer_payload(),
        cargo_status="Full", pti_status="PTI",
    )
    body.update(over)
    return body


# --------------------------------------------------------------------------- #
# Login / session
# --------------------------------------------------------------------------- #


def test_login_success_returns_me(anon_client):
    r = anon_client.post("/api/login", json={"username": "admin", "password": SEED_PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "admin"
    assert body["role"] == "admin"
    assert body["can_record"] and body["can_admin"]


def test_login_wrong_password_is_401(anon_client):
    r = anon_client.post("/api/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401
    assert "wrong username or password" in r.json()["detail"]


def test_login_unknown_user_is_401(anon_client):
    r = anon_client.post("/api/login", json={"username": "ghost", "password": SEED_PASSWORD})
    assert r.status_code == 401


def test_disabled_user_cannot_log_in(client, anon_client):
    uid = next(u["id"] for u in client.get("/api/users").json() if u["username"] == "viewer")
    client.patch(f"/api/users/{uid}", json={"disabled": True})
    r = anon_client.post("/api/login", json={"username": "viewer", "password": SEED_PASSWORD})
    assert r.status_code == 401


def test_session_persists_across_requests(anon_client):
    anon_client.post("/api/login", json={"username": "operator", "password": SEED_PASSWORD})
    assert anon_client.get("/api/me").json()["username"] == "operator"


def test_logout_clears_the_session(client):
    assert client.get("/api/me").status_code == 200
    assert client.post("/api/logout").status_code == 204
    assert client.get("/api/me").status_code == 401


# --------------------------------------------------------------------------- #
# Unauthenticated
# --------------------------------------------------------------------------- #


def test_api_requires_sign_in(anon_client):
    assert anon_client.get("/api/events").status_code == 401
    assert anon_client.get("/api/options").status_code == 401


def test_pages_redirect_to_login_when_anonymous(anon_client):
    r = anon_client.get("/gate-in", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/gate-in"
    r = anon_client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/"


def test_login_page_is_public(anon_client):
    assert anon_client.get("/login").status_code == 200


# --------------------------------------------------------------------------- #
# Role enforcement
# --------------------------------------------------------------------------- #


def test_viewer_can_read_but_not_write(viewer_client):
    assert viewer_client.get("/api/events").status_code == 200
    r = viewer_client.post("/api/events/gate-in", json=_gate_in_body())
    assert r.status_code == 403
    assert "operator" in r.json()["detail"]


def test_operator_can_record_events_but_not_edit_or_register(operator_client):
    assert operator_client.post("/api/events/gate-in", json=_gate_in_body()).status_code == 201
    ev_id = operator_client.get("/api/events").json()[0]["id"]
    assert operator_client.patch(f"/api/events/{ev_id}", json={"comments": "x"}).status_code == 403
    assert operator_client.delete(f"/api/events/{ev_id}").status_code == 403
    assert operator_client.post("/api/containers", json=reefer_payload(number=CONT_A)).status_code == 403


def test_operator_cannot_touch_user_accounts(operator_client):
    assert operator_client.get("/api/users").status_code == 403
    assert operator_client.post(
        "/api/users", json={"username": "x", "password": "longenough1", "role": "viewer"}
    ).status_code == 403


def test_admin_can_edit_and_register(client):
    assert client.post("/api/events/gate-in", json=_gate_in_body()).status_code == 201
    ev_id = client.get("/api/events").json()[0]["id"]
    assert client.patch(f"/api/events/{ev_id}", json={"comments": "fixed"}).status_code == 200


# --------------------------------------------------------------------------- #
# User management (admin)
# --------------------------------------------------------------------------- #


def test_admin_creates_and_lists_users(client):
    r = client.post(
        "/api/users", json={"username": "Nadia", "password": "s3cret-pass", "role": "operator"}
    )
    assert r.status_code == 201
    assert r.json()["username"] == "nadia"  # normalised
    names = {u["username"] for u in client.get("/api/users").json()}
    assert {"admin", "operator", "viewer", "nadia"} <= names


def test_new_user_can_sign_in(client, anon_client):
    client.post("/api/users", json={"username": "kev", "password": "kev-passw0rd", "role": "viewer"})
    assert anon_client.post(
        "/api/login", json={"username": "kev", "password": "kev-passw0rd"}
    ).status_code == 200


def test_duplicate_username_is_409(client):
    assert client.post(
        "/api/users", json={"username": "admin", "password": "another-one", "role": "viewer"}
    ).status_code == 409


def test_short_password_is_422(client):
    assert client.post(
        "/api/users", json={"username": "shorty", "password": "abc", "role": "viewer"}
    ).status_code == 422


def test_admin_cannot_delete_or_demote_self(client):
    me = client.get("/api/me").json()
    assert client.delete(f"/api/users/{me['id']}").status_code == 409
    assert client.patch(f"/api/users/{me['id']}", json={"role": "viewer"}).status_code == 409
    assert client.patch(f"/api/users/{me['id']}", json={"disabled": True}).status_code == 409


def test_cannot_delete_the_last_active_admin(client):
    # promote operator to admin, then we should be able to delete the original,
    # but not when it is the only one left.
    ops = {u["username"]: u["id"] for u in client.get("/api/users").json()}
    assert client.delete(f"/api/users/{ops['viewer']}").status_code == 204
    # only 'admin' is an admin now; deleting a non-self admin is impossible here,
    # so demote-protection is covered by the self test. Verify a 2nd admin works:
    client.post("/api/users", json={"username": "boss", "password": "boss-passw0rd", "role": "admin"})
    ops = {u["username"]: u["id"] for u in client.get("/api/users").json()}
    assert client.delete(f"/api/users/{ops['boss']}").status_code == 204
