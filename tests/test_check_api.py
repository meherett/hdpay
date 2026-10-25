"""The admin's Check payments button and the deposit's on-chain view."""

import pytest

from factories import key_headers, make_project, make_user, sign_in
from api.db import Status, get_db, set_counter

PRODUCTS = [{"value": "100k", "name": "$100,000", "subname": None}]


@pytest.fixture
def deposit(client, mongo):
    project = make_project(mongo, products=PRODUCTS)
    reply = client.post("/v1/deposits", headers=key_headers(mongo, project),
                        json={"account_id": "42", "chain": "tron", "asset": "USDT", "product": "100k"})
    assert reply.status_code == 200, reply.text
    return reply.json()


@pytest.mark.parametrize("role", ["admin", "owner"])
def test_admins_and_owners_ask_for_a_check(client, mongo, deposit, role):
    make_user(mongo, email="support@example.com", role=role, name="Sam")
    reply = client.post(f"/admin/deposits/{deposit['id']}/check", headers=sign_in(client, "support@example.com"))
    assert reply.status_code == 200, reply.text
    check = reply.json()["check"]
    assert (check["status"], check["requested_by"], check["found"]) == ("waiting", "Sam", [])
    # the watcher picks it up from the deposit
    assert get_db().deposits.find_one({"_id": deposit["id"]})["check"]["status"] == "waiting"


def test_a_viewer_cannot_ask_for_a_check(client, mongo, deposit):
    make_user(mongo, email="viewer@example.com", role="viewer", name="Vic")
    reply = client.post(f"/admin/deposits/{deposit['id']}/check", headers=sign_in(client, "viewer@example.com"))
    assert reply.status_code == 403


def test_a_paid_deposit_needs_no_check(client, mongo, deposit):
    make_user(mongo, role="admin")
    get_db().deposits.update_one({"_id": deposit["id"]}, {"$set": {"status": Status.CONFIRMED.value}})
    reply = client.post(f"/admin/deposits/{deposit['id']}/check", headers=sign_in(client))
    assert reply.status_code == 409 and "paid already" in reply.text


def test_the_on_chain_view_reads_the_balance_and_the_watcher(client, mongo, deposit, monkeypatch):
    make_user(mongo, role="viewer")
    monkeypatch.setattr("api.admin.balance", lambda chain, asset, address: 7_900_000)
    set_counter(get_db(), "heartbeat:tron", 1_800_000_000)
    view = client.get(f"/admin/deposits/{deposit['id']}/chain", headers=sign_in(client)).json()
    assert view["balance"] == "7900000" and view["error"] is None and view["watcher_seen_at"].startswith("2027-01-15")


def test_the_on_chain_view_says_plainly_when_the_rpc_fails(client, mongo, deposit, monkeypatch):
    make_user(mongo, role="admin")

    def broken(chain, asset, address):
        raise ConnectionError("https://secret-token@node.example/ is down")

    monkeypatch.setattr("api.admin.balance", broken)
    view = client.get(f"/admin/deposits/{deposit['id']}/chain", headers=sign_in(client)).json()
    assert view["balance"] is None and view["error"] == "Connection not working"
    assert "secret" not in str(view)   # an RPC URL carries an access token: never shown


def test_the_deposit_lists_its_payments(client, mongo, deposit):
    make_user(mongo, role="admin")
    get_db().deposits.update_one({"_id": deposit["id"]}, {"$set": {"payments": [
        {"tx_hash": "abc", "block_number": 7, "amount": "7900000", "paid_at": None, "source": "scan", "confirmed": True},
    ]}})
    got = client.get(f"/admin/deposits/{deposit['id']}", headers=sign_in(client)).json()
    [payment] = got["payments"]
    assert (payment["tx_hash"], payment["amount"], payment["tx_url"]) == ("abc", "7900000", "https://tronscan.org/#/transaction/abc")


def test_a_deposit_whose_coin_left_the_chains_file_still_lists(client, mongo, deposit):
    # USDC taken out of chains.yaml after a deposit was opened in it: the list must not break
    make_user(mongo, role="admin")
    get_db().deposits.update_one({"_id": deposit["id"]}, {"$set": {"asset": "USDC"}, "$unset": {"decimals": ""}})
    reply = client.get("/admin/deposits", headers=sign_in(client))
    assert reply.status_code == 200, reply.text
    [row] = [r for r in reply.json()["items"] if r["id"] == deposit["id"]]
    assert (row["asset"], row["asset_supported"], row["decimals"]) == ("USDC", False, 0)
    assert client.get(f"/admin/deposits/{deposit['id']}", headers=sign_in(client)).status_code == 200


def test_a_deposit_keeps_its_coins_decimals(client, mongo, deposit):
    stored = get_db().deposits.find_one({"_id": deposit["id"]})
    assert stored["decimals"] == 6                 # USDT on Tron, kept with the deposit
