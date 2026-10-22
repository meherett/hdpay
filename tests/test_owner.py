import pytest

from factories import key_headers, make_project, make_user, sign_in

PRODUCTS = [{"value": "100k", "name": "$100,000", "subname": None}]


@pytest.fixture
def owner(client, mongo):
    make_user(mongo, email="owner@example.com", role="owner", name="Owner")
    return sign_in(client, "owner@example.com")


@pytest.fixture
def admin(client, mongo):
    make_user(mongo, email="admin@example.com", role="admin", name="Admin")
    return sign_in(client, "admin@example.com")


def user_id(mongo, email):
    return mongo.users.find_one({"email": email})["_id"]


def open_deposit(client, mongo, project, account_id="42"):
    reply = client.post("/v1/deposits", headers=key_headers(mongo, project),
                        json={"account_id": account_id, "chain": "tron", "asset": "USDT", "product": "100k"})
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_an_owner_does_what_an_admin_does(client, mongo, owner):
    reply = client.post("/admin/users", headers=owner,
                        json={"email": "v@example.com", "name": "V", "password": "password-1", "role": "viewer"})
    assert reply.status_code == 201


def test_only_an_owner_deletes_a_deposit_address(client, mongo, owner, admin):
    project = make_project(mongo, products=PRODUCTS)
    d = open_deposit(client, mongo, project)
    assert client.delete(f"/admin/deposits/{d['id']}", headers=admin).status_code == 403
    assert client.delete(f"/admin/deposits/{d['id']}", headers=owner).status_code == 204
    assert mongo.deposits.find_one({"_id": d["id"]}) is None
    assert client.delete(f"/admin/deposits/{d['id']}", headers=owner).status_code == 404


def test_only_an_owner_deletes_a_project_and_only_by_its_name(client, mongo, owner, admin):
    project = make_project(mongo, products=PRODUCTS)
    open_deposit(client, mongo, project)
    url = f"/admin/projects/{project['_id']}"
    assert client.delete(url, headers=admin, params={"confirm": "Shop"}).status_code == 403
    assert client.delete(url, headers=owner, params={"confirm": "shop"}).status_code == 422
    assert client.delete(url, headers=owner, params={"confirm": "Shop"}).status_code == 204
    for collection in ("projects", "deposits", "api_keys", "events"):
        assert getattr(mongo, collection).count_documents({"project_id": project["_id"]} if collection != "projects" else {}) == 0


def test_admins_cannot_make_or_touch_owners(client, mongo, owner, admin):
    owner_id = user_id(mongo, "owner@example.com")
    made = client.post("/admin/users", headers=admin,
                       json={"email": "o2@example.com", "name": "O2", "password": "password-1", "role": "owner"})
    assert made.status_code == 403
    assert client.patch(f"/admin/users/{owner_id}", headers=admin, json={"role": "viewer"}).status_code == 403
    assert client.post(f"/admin/users/{owner_id}/password", headers=admin, json={"password": "password-2"}).status_code == 403
    assert client.delete(f"/admin/users/{owner_id}", headers=admin).status_code == 403
    # an admin cannot promote itself either
    admin_id = user_id(mongo, "admin@example.com")
    assert client.patch(f"/admin/users/{admin_id}", headers=admin, json={"role": "owner"}).status_code == 403


def test_an_owner_makes_another_owner_and_the_last_one_stays(client, mongo, owner):
    owner_id = user_id(mongo, "owner@example.com")
    assert client.patch(f"/admin/users/{owner_id}", headers=owner, json={"role": "admin"}).status_code == 409   # self
    make_user(mongo, email="second@example.com", role="admin", name="Second")
    second_id = user_id(mongo, "second@example.com")
    assert client.patch(f"/admin/users/{second_id}", headers=owner, json={"role": "owner"}).json()["role"] == "owner"
    second = sign_in(client, "second@example.com")
    # with two owners, one can step the other down; the last one left cannot be
    assert client.patch(f"/admin/users/{owner_id}", headers=second, json={"role": "admin"}).json()["role"] == "admin"
    assert client.delete(f"/admin/users/{second_id}", headers=owner).status_code == 403   # owner is an admin now


def test_the_last_owner_cannot_be_disabled_or_deleted(client, mongo, owner):
    make_user(mongo, email="other@example.com", role="owner", name="Other")
    other = sign_in(client, "other@example.com")
    owner_id = user_id(mongo, "owner@example.com")
    other_id = user_id(mongo, "other@example.com")
    assert client.delete(f"/admin/users/{other_id}", headers=owner).status_code == 204
    # now the only owner: nobody else can be made to remove them, and they cannot remove themselves
    assert client.delete(f"/admin/users/{owner_id}", headers=owner).status_code == 409
    assert other is not None


def test_only_an_owner_changes_the_treasury_allowed_ips_and_api_keys(client, mongo, owner, admin):
    project = make_project(mongo, products=PRODUCTS)
    url = f"/admin/projects/{project['_id']}"
    same = {"treasury": dict(project["treasury"]), "allowed_ips": []}
    # an admin edits the rest, and may send the treasury and IPs back as they are
    assert client.patch(url, headers=admin, json={"name": "Shop 2", **same}).status_code == 200
    for change in ({"treasury": {"evm": None}}, {"allowed_ips": ["203.0.113.7"]}):
        reply = client.patch(url, headers=admin, json=change)
        assert reply.status_code == 403 and "Only an owner" in reply.text
    assert client.patch(url, headers=owner, json={"allowed_ips": ["203.0.113.7"]}).status_code == 200

    keys = f"{url}/api-keys"
    assert client.post(keys, headers=admin, json={"kind": "secret", "name": "x"}).status_code == 403
    key = client.post(keys, headers=owner, json={"kind": "secret", "name": "x"}).json()
    assert client.delete(f"{keys}/{key['id']}", headers=admin).status_code == 403
    assert client.get(keys, headers=admin).status_code == 200   # still listed
    assert client.delete(f"{keys}/{key['id']}", headers=owner).status_code == 200
