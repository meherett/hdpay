import pytest

from factories import EVM_TREASURY, MASTER, TRON_TREASURY, key_headers, make_project, make_user, sign_in
from api.chains import Family
from api.keys import MAX_INDEX, project_xpub

# a product without a price, as the admin returns it
NO_PRICE = {"price": None}

PRODUCTS = [
    {"value": "10k", "name": "$10,000", "subname": None},
    {"value": "100k", "name": "$100,000", "subname": "(Most popular)"},
]


@pytest.fixture
def admin(client, mongo):
    """An owner: only owners create projects and set their keys."""
    make_user(mongo, role="owner")
    return sign_in(client)


def create(client, admin, **fields):
    body = {
        "name": "Shop",
        "products": PRODUCTS,
        "treasury": {"evm": EVM_TREASURY, "tron": TRON_TREASURY},
        "chains": {"sepolia": ["ETH", "USDC"], "tron-nile": ["USDT"]},
        **fields,
    }
    return client.post("/admin/projects", headers=admin, json=body)


def test_a_new_project_gets_a_random_number_and_waits_for_its_keys(client, admin):
    reply = create(client, admin)
    assert reply.status_code == 201
    project = reply.json()
    assert 1 <= project["number"] <= MAX_INDEX and project["status"] == "awaiting_keys"
    n = project["number"]
    assert project["key_paths"] == {"evm": f"m/{n}'/60'", "tron": f"m/{n}'/195'"}
    assert project["products"] == [{**p, **NO_PRICE} for p in PRODUCTS] and project["chains"] == {"sepolia": ["ETH", "USDC"], "tron-nile": ["USDT"]}
    assert project["deposit_ttl_minutes"] == 30 and project["remainder_window_minutes"] == 1440
    assert create(client, admin).json()["number"] != n


@pytest.mark.parametrize("fields, problem", [
    ({"products": [{"value": "a", "name": "A"}, {"value": "a", "name": "B"}]}, "must be unique: a"),
    ({"products": [{"value": "a", "name": "A", "colour": "blue"}]}, "Extra inputs"),
    ({"treasury": {"evm": "0x123"}}, "not a valid evm address"),
    ({"treasury": {"tron": EVM_TREASURY}}, "not a valid tron address"),
    ({"allowed_ips": ["not-an-ip"]}, "not an IP address"),
    ({"amount_tolerance_bps": -1}, "greater than or equal"),
    ({"website": "example.com"}, "not a web address"),
    ({"website": "ftp://example.com"}, "not a web address"),
    ({"description": "x" * 501}, "at most 500 characters"),
])
def test_project_fields_are_checked(client, admin, fields, problem):
    reply = create(client, admin, **fields)
    assert reply.status_code == 422 and problem in reply.text


def test_a_coin_taken_out_of_the_chains_file_does_not_stop_a_save(client, admin, mongo):
    # USDC on BNB Smart Chain chosen once, then taken out of chains.yaml: the admin sends it back on every save
    project = create(client, admin).json()
    mongo.projects.update_one({"_id": project["id"]}, {"$set": {"chains": {"sepolia": ["ETH", "USDX"], "gone": ["GONE"]}}})
    saved = client.patch(f"/admin/projects/{project['id']}", headers=admin,
                         json={"name": "Renamed", "chains": {"sepolia": ["ETH", "USDX"], "gone": ["GONE"]}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["name"] == "Renamed" and saved.json()["chains"] == {"sepolia": ["ETH"]}   # what is listed stays


def test_keys_activate_the_project_and_cannot_change_once_used(client, admin, mongo):
    project = create(client, admin).json()
    url = f"/admin/projects/{project['id']}/keys"
    n = project["number"]
    wrong = client.post(url, headers=admin, json={"evm": project_xpub(MASTER, n, Family.TRON)})
    assert wrong.status_code == 422 and "m/<project>'/60'" in wrong.text
    keyed = client.post(url, headers=admin, json={"evm": project_xpub(MASTER, n, Family.EVM)}).json()
    assert keyed["status"] == "active" and keyed["xpubs"]["tron"] is None
    # the other family can follow later
    both = client.post(url, headers=admin, json={"tron": project_xpub(MASTER, n, Family.TRON)}).json()
    assert both["xpubs"]["tron"] and both["xpubs"]["evm"] == keyed["xpubs"]["evm"]

    deposit = client.post("/v1/deposits", headers=key_headers(mongo, mongo.projects.find_one({"_id": project["id"]})),
                          json={"account_id": "1", "chain": "sepolia", "asset": "ETH", "product": "10k"})
    assert deposit.status_code == 200
    other = client.post(url, headers=admin, json={"evm": project_xpub(MASTER, n + 1, Family.EVM)})
    assert other.status_code == 409


def test_editing_a_project(client, admin):
    project = create(client, admin).json()
    url = f"/admin/projects/{project['id']}"
    changed = client.patch(url, headers=admin, json={
        "name": "Shop 2", "products": [{"value": "x", "name": "X"}], "remainder_window_minutes": 30,
        "allowed_ips": ["203.0.113.7", "198.51.100.0/24"],
    }).json()
    assert changed["name"] == "Shop 2" and changed["products"] == [{"value": "x", "name": "X", "subname": None, **NO_PRICE}]
    assert changed["remainder_window_minutes"] == 30
    assert changed["allowed_ips"] == ["203.0.113.7/32", "198.51.100.0/24"]
    assert client.patch(url, headers=admin, json={"name": None}).status_code == 422
    assert client.patch(url, headers=admin, json={}).status_code == 422


def test_api_keys_are_shown_once_and_can_be_revoked(client, admin, mongo):
    project = make_project(mongo)
    url = f"/admin/projects/{project['_id']}/api-keys"
    made = client.post(url, headers=admin, json={"kind": "secret", "name": "backend"})
    assert made.status_code == 201
    key = made.json()["key"]
    assert key.startswith("hdp_sk_") and made.json()["prefix"] == key[:12]
    assert mongo.api_keys.find_one({"_id": made.json()["id"]}).get("key") is None   # only a hash is stored
    listed = client.get(url, headers=admin).json()
    assert len(listed) == 1 and "key" not in listed[0]

    gateway = {"Authorization": f"Bearer {key}"}
    assert client.get("/v1/products", headers=gateway).status_code == 200
    assert client.delete(f"{url}/{made.json()['id']}", headers=admin).json()["revoked_at"]
    assert client.get("/v1/products", headers=gateway).status_code == 401


def test_a_disabled_project_is_refused_but_needs_keys_to_enable(client, admin, mongo):
    project = make_project(mongo)
    headers = key_headers(mongo, project)
    url = f"/admin/projects/{project['_id']}/status"
    assert client.post(url, headers=admin, params={"status": "disabled"}).json()["status"] == "disabled"
    assert client.get("/v1/products", headers=headers).status_code == 403
    assert client.post(url, headers=admin, params={"status": "active"}).json()["status"] == "active"
    waiting = create(client, admin).json()
    assert client.post(f"/admin/projects/{waiting['id']}/status", headers=admin, params={"status": "active"}).status_code == 409


def test_allowed_ips_limit_where_keys_work(client, admin, mongo):
    project = make_project(mongo, allowed_ips=["203.0.113.0/24"])
    headers = key_headers(mongo, project)
    reply = client.get("/v1/products", headers=headers)     # the test client calls from "testclient"
    assert reply.status_code == 403 and "not allowed" in reply.text
    mongo.projects.update_one({"_id": project["_id"]}, {"$set": {"allowed_ips": []}})
    assert client.get("/v1/products", headers=headers).status_code == 200


def test_only_an_owner_creates_a_project_or_sets_its_keys(client, mongo):
    make_user(mongo, email="plain@example.com", role="admin")
    plain = sign_in(client, "plain@example.com")
    assert create(client, plain).status_code == 403
    make_user(mongo, email="boss@example.com", role="owner")
    project = create(client, sign_in(client, "boss@example.com")).json()
    keys = {"evm": project_xpub(MASTER, project["number"], Family.EVM)}
    assert client.post(f"/admin/projects/{project['id']}/keys", headers=plain, json=keys).status_code == 403


def test_a_sweeper_gives_a_new_project_its_keys(client, mongo, admin):
    from stubs import StubSweeper
    from sweeper import core

    project = create(client, admin).json()
    assert project["status"] == "awaiting_keys"
    sweeper = StubSweeper()
    sweeper.master_xprv = MASTER
    core.sweep_once(mongo, sweeper)
    saved = mongo.projects.find_one({"_id": project["id"]})
    assert saved["status"] == "active"
    assert saved["xpubs"] == {f.value: project_xpub(MASTER, project["number"], f) for f in Family}
    # a project that has its keys is left alone
    mongo.projects.update_one({"_id": project["id"]}, {"$set": {"xpubs.evm": "kept", "status": "disabled"}})
    core.sweep_once(mongo, sweeper)
    assert mongo.projects.find_one({"_id": project["id"]})["xpubs"]["evm"] == "kept"


def test_a_project_says_when_it_last_changed(client, mongo, admin):
    project = create(client, admin).json()
    assert project["updated_at"] == project["created_at"]
    changed = client.patch(f"/admin/projects/{project['id']}", headers=admin, json={"name": "Shop 2"}).json()
    assert changed["updated_at"] > project["updated_at"]
    assert client.get("/admin/projects", headers=admin).json()[0]["updated_at"] == changed["updated_at"]


def test_a_project_id_is_a_mongodb_object_id(client, mongo, admin):
    from bson import ObjectId

    project = create(client, admin).json()
    assert ObjectId.is_valid(project["id"]) and len(project["id"]) == 24


def test_old_project_ids_move_with_everything_that_names_them(mongo):
    from bson import ObjectId

    from api.projects import migrate_project_ids

    old = "prj_f6ac9e7520f608cd"
    mongo.projects.insert_one({"_id": old, "name": "Old", "number": 1})
    mongo.deposits.insert_one({"_id": "d1", "project_id": old, "address": "TOld"})
    mongo.api_keys.insert_one({"_id": "k1", "project_id": old, "key_hash": "h"})
    mongo.events.insert_one({"_id": "e1", "project_id": old, "seq": 1})
    mongo.users.insert_one({"_id": "u1", "email": "a@example.com", "projects": [old, "other"]})
    mongo.counters.insert_many([
        {"_id": f"address_index:{old}:tron:2026-10-21:42", "value": 3},
        {"_id": f"event_seq:{old}", "value": 9},
        {"_id": "last_block:tron", "value": 100},
    ])
    [(was, new)] = migrate_project_ids(mongo)
    assert was == old and ObjectId.is_valid(new)
    assert mongo.projects.find_one({"_id": old}) is None and mongo.projects.find_one({"_id": new})["name"] == "Old"
    for collection in ("deposits", "api_keys", "events"):
        assert mongo[collection].find_one()["project_id"] == new
    assert mongo.users.find_one({"_id": "u1"})["projects"] == [new, "other"]
    # the counters move too, so the next address index continues where it was
    assert mongo.counters.find_one({"_id": f"address_index:{new}:tron:2026-10-21:42"})["value"] == 3
    assert mongo.counters.find_one({"_id": f"event_seq:{new}"})["value"] == 9
    assert mongo.counters.count_documents({"_id": {"$regex": old}}) == 0
    assert migrate_project_ids(mongo) == []   # nothing left to move


def test_a_project_has_a_description_and_a_website(client, admin):
    project = create(client, admin, description="  Prop firm challenges  ", website=" https://shop.example.com ").json()
    assert (project["description"], project["website"]) == ("Prop firm challenges", "https://shop.example.com")
    url = f"/admin/projects/{project['id']}"
    # blank clears them; leaving them out keeps them
    assert client.patch(url, headers=admin, json={"name": "Shop 2"}).json()["website"] == "https://shop.example.com"
    cleared = client.patch(url, headers=admin, json={"description": "", "website": None}).json()
    assert (cleared["description"], cleared["website"]) == (None, None)
    assert create(client, admin).json()["description"] is None
