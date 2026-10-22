"""Admins and viewers see the projects an owner gave them: all of them, or some."""

import pytest

from factories import NUMBER, key_headers, make_project, make_user, sign_in

PRODUCTS = [{"value": "100k", "name": "$100,000", "subname": None}]


@pytest.fixture
def owner(client, mongo):
    make_user(mongo, email="owner@example.com", role="owner", name="Owner")
    return sign_in(client, "owner@example.com")


@pytest.fixture
def shops(mongo):
    return make_project(mongo, products=PRODUCTS), make_project(mongo, number=NUMBER + 1, name="Other", products=PRODUCTS)


def open_deposit(client, mongo, project):
    reply = client.post("/v1/deposits", headers=key_headers(mongo, project),
                        json={"account_id": "42", "chain": "tron", "asset": "USDT", "product": "100k"})
    assert reply.status_code == 200, reply.text
    return reply.json()


def user(client, owner, role, projects):
    email = f"{role}-{len(projects or [])}@example.com"
    made = client.post("/admin/users", headers=owner,
                       json={"email": email, "name": role, "password": "password-1", "role": role, "projects": projects})
    assert made.status_code == 201, made.text
    reply = client.post("/auth/login", json={"email": email, "password": "password-1"})
    return made.json(), {"Authorization": f"Bearer {reply.json()['access_token']}"}


def test_a_user_with_some_projects_sees_only_those(client, mongo, owner, shops):
    mine, other = shops
    in_mine, in_other = open_deposit(client, mongo, mine), open_deposit(client, mongo, other)
    made, headers = user(client, owner, "admin", [mine["_id"]])
    assert made["projects"] == [mine["_id"]]

    listed = {p["id"]: p for p in client.get("/admin/projects", headers=headers).json()}
    assert listed[mine["_id"]]["status"] == "active"
    # the other project is listed, but only its name and number
    hidden = listed[other["_id"]]
    assert (hidden["status"], hidden["name"], hidden["number"]) == ("locked", "Other", NUMBER + 1)
    assert (hidden["chains"], hidden["products"], hidden["xpubs"], hidden["created_at"]) == ({}, [], {}, None)
    assert client.get(f"/admin/projects/{other['_id']}", headers=headers).status_code == 404
    assert client.get(f"/admin/projects/{other['_id']}/events", headers=headers).status_code == 404
    assert client.patch(f"/admin/projects/{other['_id']}", headers=headers, json={"name": "x"}).status_code == 404
    assert [d["id"] for d in client.get("/admin/deposits", headers=headers).json()["items"]] == [in_mine["id"]]
    assert client.get(f"/admin/deposits/{in_other['id']}", headers=headers).status_code == 404
    assert client.post(f"/admin/deposits/{in_other['id']}/credit", headers=headers).status_code == 404
    stats = client.get("/admin/stats", headers=headers).json()
    assert (stats["total"], stats["projects"]) == (1, 1)
    assert client.get("/admin/stats", headers=headers, params={"project_id": other["_id"]}).status_code == 404


def test_all_projects_is_the_default_and_can_be_set_again(client, mongo, owner, shops):
    made, headers = user(client, owner, "viewer", None)
    assert made["projects"] is None
    assert len(client.get("/admin/projects", headers=headers).json()) == 2
    url = f"/admin/users/{made['id']}"
    assert client.patch(url, headers=owner, json={"projects": [shops[1]["_id"]]}).json()["projects"] == [shops[1]["_id"]]
    statuses = {p["name"]: p["status"] for p in client.get("/admin/projects", headers=headers).json()}
    assert statuses == {"Shop": "locked", "Other": "active"}   # at once
    assert client.patch(url, headers=owner, json={"projects": "all"}).json()["projects"] is None
    assert len(client.get("/admin/projects", headers=headers).json()) == 2


def test_projects_must_exist_and_be_at_least_one(client, mongo, owner, shops):
    body = {"email": "x@example.com", "name": "X", "password": "password-1", "role": "admin"}
    assert client.post("/admin/users", headers=owner, json=body | {"projects": ["0" * 24]}).status_code == 422
    assert client.post("/admin/users", headers=owner, json=body | {"projects": []}).status_code == 422


def test_owners_always_see_everything_and_a_deleted_project_leaves_the_lists(client, mongo, owner, shops):
    mine, other = shops
    made, _ = user(client, owner, "admin", [mine["_id"], other["_id"]])
    promoted = client.patch(f"/admin/users/{made['id']}", headers=owner, json={"role": "owner"}).json()
    assert promoted["projects"] is None
    demoted = client.patch(f"/admin/users/{made['id']}", headers=owner, json={"role": "admin", "projects": [mine["_id"], other["_id"]]})
    assert demoted.json()["projects"] == [mine["_id"], other["_id"]]
    assert client.delete(f"/admin/projects/{other['_id']}", headers=owner, params={"confirm": "Other"}).status_code == 204
    assert mongo.users.find_one({"_id": made["id"]})["projects"] == [mine["_id"]]
