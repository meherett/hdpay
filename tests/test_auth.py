import pytest

from factories import PASSWORD, make_user, sign_in
from api.config import settings


def test_sign_in_returns_tokens_and_the_user(client, mongo):
    make_user(mongo)
    reply = client.post("/auth/login", json={"email": " ADMIN@example.com ", "password": PASSWORD})
    assert reply.status_code == 200
    body = reply.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == settings.access_token_minutes * 60
    assert body["user"]["email"] == "admin@example.com" and body["user"]["role"] == "admin"
    assert "password_hash" not in body["user"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["name"] == "Ada"
    # only a hash of the refresh token is stored
    assert mongo.sessions.find_one()["token_hash"] != body["refresh_token"]


def test_wrong_password_and_unknown_email_get_the_same_answer(client, mongo):
    make_user(mongo)
    wrong = client.post("/auth/login", json={"email": "admin@example.com", "password": "nope-nope"})
    unknown = client.post("/auth/login", json={"email": "who@example.com", "password": "nope-nope"})
    assert (wrong.status_code, wrong.json()) == (unknown.status_code, unknown.json()) == (401, {"detail": "Wrong email or password"})


def test_too_many_wrong_passwords_pause_sign_in(client, mongo):
    make_user(mongo)
    for _ in range(settings.max_login_attempts):
        assert client.post("/auth/login", json={"email": "admin@example.com", "password": "nope-nope"}).status_code == 401
    blocked = client.post("/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0


def test_a_refresh_token_works_once(client, mongo):
    make_user(mongo)
    first = client.post("/auth/login", json={"email": "admin@example.com", "password": PASSWORD}).json()
    second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200 and second.json()["refresh_token"] != first["refresh_token"]
    assert client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401


def test_sign_out_ends_the_session(client, mongo):
    make_user(mongo)
    tokens = client.post("/auth/login", json={"email": "admin@example.com", "password": PASSWORD}).json()
    assert client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_changing_the_password_ends_every_session(client, mongo):
    make_user(mongo)
    tokens = client.post("/auth/login", json={"email": "admin@example.com", "password": PASSWORD}).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.post("/auth/change-password", headers=headers,
                       json={"old_password": "wrong-one", "new_password": "a new password"}).status_code == 400
    assert client.post("/auth/change-password", headers=headers,
                       json={"old_password": PASSWORD, "new_password": "a new password"}).status_code == 204
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.post("/auth/login", json={"email": "admin@example.com", "password": "a new password"}).status_code == 200


def test_no_token_or_a_bad_one_is_refused(client, mongo):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/admin/projects", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401


def test_sign_in_is_off_without_a_jwt_secret(client, mongo, monkeypatch):
    make_user(mongo)
    monkeypatch.setattr(settings, "jwt_secret", "")
    assert client.post("/auth/login", json={"email": "admin@example.com", "password": PASSWORD}).status_code == 503


def test_a_disabled_user_cannot_sign_in(client, mongo):
    make_user(mongo, email="off@example.com")
    mongo.users.update_one({"email": "off@example.com"}, {"$set": {"is_active": False}})
    assert client.post("/auth/login", json={"email": "off@example.com", "password": PASSWORD}).status_code == 403


def test_a_viewer_reads_but_cannot_change(client, mongo):
    make_user(mongo, email="viewer@example.com", role="viewer")
    headers = sign_in(client, "viewer@example.com")
    assert client.get("/admin/projects", headers=headers).status_code == 200
    created = client.post("/admin/projects", headers=headers, json={"name": "Shop"})
    assert created.status_code == 403


def test_owners_manage_users(client, mongo):
    admin = make_user(mongo, role="owner")
    headers = sign_in(client)
    made = client.post("/admin/users", headers=headers,
                       json={"email": "Bob@Example.com", "name": "Bob", "password": "bob-password", "role": "viewer"})
    assert made.status_code == 201 and made.json()["email"] == "bob@example.com"
    duplicate = client.post("/admin/users", headers=headers,
                            json={"email": "bob@example.com", "name": "Bob", "password": "bob-password", "role": "viewer"})
    assert duplicate.status_code == 409
    bob = made.json()["id"]
    assert client.patch(f"/admin/users/{bob}", headers=headers, json={"role": "admin"}).json()["role"] == "admin"
    assert client.post(f"/admin/users/{bob}/password", headers=headers, json={"password": "short"}).status_code == 422
    assert client.post(f"/admin/users/{bob}/password", headers=headers, json={"password": "bob-new-password"}).status_code == 204
    assert client.post("/auth/login", json={"email": "bob@example.com", "password": "bob-new-password"}).status_code == 200
    # nobody locks themselves out
    me = admin["_id"]
    assert client.patch(f"/admin/users/{me}", headers=headers, json={"role": "viewer"}).status_code == 409
    assert client.patch(f"/admin/users/{me}", headers=headers, json={"is_active": False}).status_code == 409
    assert client.delete(f"/admin/users/{me}", headers=headers).status_code == 409
    assert client.delete(f"/admin/users/{bob}", headers=headers).status_code == 204
    assert [u["email"] for u in client.get("/admin/users", headers=headers).json()] == ["admin@example.com"]


def test_admins_and_viewers_cannot_see_or_manage_users(client, mongo):
    for role in ("admin", "viewer"):
        make_user(mongo, email=f"{role}@example.com", role=role)
        headers = sign_in(client, f"{role}@example.com")
        assert client.get("/admin/users", headers=headers).status_code == 403
        made = client.post("/admin/users", headers=headers,
                           json={"email": "x@example.com", "name": "X", "password": "x-password", "role": "viewer"})
        assert made.status_code == 403


@pytest.mark.parametrize("password", ["short", "x" * 73])
def test_passwords_are_8_to_72_characters(client, mongo, password):
    make_user(mongo, role="owner")
    reply = client.post("/admin/users", headers=sign_in(client),
                        json={"email": "c@example.com", "name": "C", "password": password, "role": "viewer"})
    assert reply.status_code == 422
