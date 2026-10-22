from datetime import timedelta

import pytest

from factories import EVM_TREASURY, NUMBER, TRON_TREASURY, key_headers, make_project, make_user, sign_in
from api import balances
from api.chains import CHAINS
from api.db import Status, get_db, utcnow

PRODUCTS = [{"value": "100k", "name": "$100,000", "subname": "(Most popular)"}, {"value": "10k", "name": "$10,000", "subname": None}]


@pytest.fixture
def admin(client, mongo):
    make_user(mongo)
    return sign_in(client)


@pytest.fixture
def project(mongo):
    return make_project(mongo, products=PRODUCTS)


def open_deposit(client, mongo, project, **body):
    reply = client.post("/v1/deposits", headers=key_headers(mongo, project),
                        json={"account_id": "42", "chain": "tron", "asset": "USDT", "product": "100k", **body})
    assert reply.status_code == 200, reply.text
    return reply.json()


def set_fields(deposit_id, **fields):
    get_db().deposits.update_one({"_id": deposit_id}, {"$set": {k: v.value if isinstance(v, Status) else v for k, v in fields.items()}})


def ids(client, admin, **params):
    return {d["id"] for d in client.get("/admin/deposits", headers=admin, params=params).json()["items"]}


def test_list_filters_and_search(client, admin, mongo, project):
    other = make_project(mongo, number=NUMBER + 1, name="Other", products=PRODUCTS)
    tron = open_deposit(client, mongo, project, account_id="7")
    bsc = open_deposit(client, mongo, project, account_id="8", chain="bsc", asset="USDC", product="10k")
    trx = open_deposit(client, mongo, project, account_id="9", asset="TRX")
    elsewhere = open_deposit(client, mongo, other, account_id="7")
    listed = client.get("/admin/deposits", headers=admin).json()
    assert listed["total"] == 4 and listed["items"][0]["id"] == elsewhere["id"]          # newest first
    assert listed["items"][0]["project_name"] == "Other" and listed["items"][0]["path"].startswith(f"m/{NUMBER + 1}'/195'/7/")
    assert ids(client, admin, project_id=project["_id"]) == {tron["id"], bsc["id"], trx["id"]}
    assert ids(client, admin, chain="bsc") == {bsc["id"]}
    assert ids(client, admin, type="native") == {trx["id"]}
    assert ids(client, admin, type="token", project_id=project["_id"]) == {tron["id"], bsc["id"]}
    # a token standard names one chain's tokens: BEP20 on BNB Smart Chain, TRC20 on Tron
    assert ids(client, admin, type="BEP20", project_id=project["_id"]) == {bsc["id"]}
    assert ids(client, admin, type="TRC20", project_id=project["_id"]) == {tron["id"]}
    assert ids(client, admin, type="ERC20", project_id=project["_id"]) == set()
    assert ids(client, admin, asset="USDC") == {bsc["id"]}
    assert ids(client, admin, status="pending", project_id=other["_id"]) == {elsewhere["id"]}
    assert ids(client, admin, search=tron["address"].lower()) == {tron["id"]}      # address, any case
    assert ids(client, admin, search="9") == {trx["id"]}                            # account id
    assert ids(client, admin, search="$10,0") == {bsc["id"]}                        # product name
    assert client.get("/admin/deposits", headers=admin, params={"type": "coin"}).status_code == 422


def test_list_filters_by_difference(client, admin, mongo, project):
    over = open_deposit(client, mongo, project, account_id="1", expected_amount=9_000_000)["id"]
    under = open_deposit(client, mongo, project, account_id="2", expected_amount=10_000_000)["id"]
    exact = open_deposit(client, mongo, project, account_id="3", expected_amount=10_000_000)["id"]
    open_deposit(client, mongo, project, account_id="4", expected_amount=10_000_000)        # nothing yet
    from api.db import find_deposit

    for deposit_id, received in ((over, "10000000"), (under, "4000000"), (exact, "10000000")):
        d = find_deposit(get_db(), deposit_id)
        d.received_amount = received
        d.save(get_db())
    assert ids(client, admin, difference="over") == {over}
    assert ids(client, admin, difference="under") == {under}


def test_stats_count_per_status_and_project(client, admin, mongo, project):
    a = open_deposit(client, mongo, project, account_id="1")
    open_deposit(client, mongo, project, account_id="2")
    set_fields(a["id"], status=Status.CONFIRMED, received_amount="2000000")
    stats = client.get("/admin/stats", headers=admin).json()
    assert stats["total"] == 2 and stats["by_status"]["confirmed"] == 1 and stats["projects"] == 1
    assert [(t["chain"], t["asset"], t["status"], t["amount"]) for t in stats["totals"]] == [("tron", "USDT", "confirmed", "2000000")]
    assert {w["chain"] for w in stats["watchers"]} == {"tron", "tron-nile", "sepolia", "bsc"}
    assert client.get("/admin/stats", headers=admin, params={"project_id": "0" * 24}).status_code == 404


def test_credit_only_from_review_or_partial_and_the_project_hears_of_it(client, admin, mongo, project):
    d = open_deposit(client, mongo, project)
    url = f"/admin/deposits/{d['id']}/credit"
    assert client.post(url, headers=admin).status_code == 409
    set_fields(d["id"], status=Status.NEEDS_REVIEW, received_amount="4000000")
    reply = client.post(url, headers=admin)
    assert reply.status_code == 200 and (reply.json()["status"], reply.json()["internal_status"]) == ("paid", "confirmed")
    assert [e["type"] for e in mongo.events.find().sort("seq", 1)] == ["deposit.created", "deposit.confirmed"]


def test_reopen_gives_a_fresh_window_with_supports_terms(client, admin, mongo, project):
    d = open_deposit(client, mongo, project, expected_amount=10_000_000)
    url = f"/admin/deposits/{d['id']}/reopen"
    assert client.post(url, headers=admin).status_code == 409             # pending: nothing to reopen
    set_fields(d["id"], status=Status.NEEDS_REVIEW, received_amount="4000000", expires_at=utcnow() - timedelta(hours=1))
    assert client.post(url, headers=admin, json={"remaining_amount": "0"}).status_code == 422
    assert client.post(url, headers=admin, json={"product": "gold"}).status_code == 422
    reply = client.post(url, headers=admin, json={"remaining_amount": "5000000", "minutes": 120, "product": "10k"}).json()
    assert (reply["internal_status"], reply["expected_amount"], reply["product"]) == ("partial", "9000000", "10k")
    assert 119 * 60 < reply["expires_in_seconds"] <= 120 * 60
    assert mongo.deposits.find_one({"_id": d["id"]})["confirmed_amount"] == "4000000"
    assert mongo.events.find_one({"type": "deposit.reopened"})["deposit"]["expected_amount"] == "9000000"
    assert client.post(url, headers=admin).status_code == 409             # already partial


def test_reopen_defaults_to_the_projects_window(client, admin, mongo, project):
    mongo.projects.update_one({"_id": project["_id"]}, {"$set": {"remainder_window_minutes": 30}})
    d = open_deposit(client, mongo, project, expected_amount=10_000_000)
    set_fields(d["id"], status=Status.NEEDS_REVIEW, received_amount="4000000")
    reply = client.post(f"/admin/deposits/{d['id']}/reopen", headers=admin).json()
    assert 29 * 60 < reply["expires_in_seconds"] <= 30 * 60 and reply["expected_amount"] == "10000000"


def test_the_admin_does_not_open_deposits(client, admin, mongo, project):
    made = client.post("/admin/deposits", headers=admin,
                       json={"project_id": project["_id"], "account_id": "5", "chain": "sepolia", "asset": "ETH", "product": "10k"})
    assert made.status_code == 405   # projects open deposits, through the gateway


def test_the_sweeper_page_and_releasing_a_hold(client, admin, mongo, project):
    waiting = open_deposit(client, mongo, project, account_id="1")
    held = open_deposit(client, mongo, project, account_id="2")
    swept = open_deposit(client, mongo, project, account_id="3")
    set_fields(waiting["id"], status=Status.CONFIRMED, confirmed_at=utcnow())
    set_fields(held["id"], status=Status.CONFIRMED, confirmed_at=utcnow(), sweep_hold="fee top-up failed on chain")
    set_fields(swept["id"], status=Status.SWEPT, swept_at=utcnow(), sweep_tx_hash="ab" * 32)
    assert client.get("/admin/sweeper", headers=admin).status_code == 403   # owners only
    make_user(mongo, email="owner@example.com", role="owner", name="Owner")
    page = client.get("/admin/sweeper", headers=sign_in(client, "owner@example.com")).json()
    assert page["counts"] == {"waiting": 1, "topping_up": 0, "sweeping": 0, "held": 1, "swept": 1}
    assert [d["id"] for d in page["recent"]] == [swept["id"]]
    released = client.post(f"/admin/deposits/{held['id']}/release", headers=admin)
    assert released.status_code == 200 and released.json()["sweep_hold"] is None
    assert client.post(f"/admin/deposits/{held['id']}/release", headers=admin).status_code == 409


def test_a_projects_treasury_and_fee_funders(client, admin, mongo, project, monkeypatch):
    def fake_balance(chain, asset, address):
        if chain.key == "bsc":
            raise TimeoutError("node did not answer at https://x.example/SECRET/")
        return {("sepolia", "USDC"): 1_240_000, ("tron-nile", "USDT"): 7}.get((chain.key, asset), 0)

    monkeypatch.setattr(balances, "balance", fake_balance)
    treasury = {t["family"]: t for t in client.get(f"/admin/projects/{project['_id']}/treasury", headers=admin).json()}
    assert treasury["evm"]["address"] == EVM_TREASURY and treasury["tron"]["address"] == TRON_TREASURY
    evm = {(b["chain"], b["asset"]): b for b in treasury["evm"]["balances"]}
    assert evm[("sepolia", "USDC")]["balance"] == "1240000"
    assert (evm[("bsc", "USDT")]["balance"], evm[("bsc", "USDT")]["error"]) == (None, "No answer in time")

    monkeypatch.setattr(balances, "native_balance", lambda chain, address: 5 * 10**17)
    funders = {f["family"]: f for f in client.get(f"/admin/projects/{project['_id']}/funders", headers=admin).json()}
    assert funders["evm"]["path"] == f"m/{NUMBER}'/60'/{utcnow().year}"
    assert {b["chain"] for b in funders["evm"]["balances"]} == {"sepolia", "bsc"}


def test_a_projects_recent_events(client, admin, mongo, project):
    open_deposit(client, mongo, project)
    events = client.get(f"/admin/projects/{project['_id']}/events", headers=admin).json()
    assert [e["type"] for e in events] == ["deposit.created"]


def test_rpc_problems_are_plain_and_never_repeat_the_connection(monkeypatch):
    class ConnectionError(OSError):   # how requests reports an unreachable node: host and path apart
        pass

    def unreachable(chain, address):
        raise ConnectionError("HTTPSConnectionPool(host='x.quiknode.pro', port=443): Max retries exceeded with url: /SECRET/")

    monkeypatch.setattr(balances, "native_balance", unreachable)
    [(balance, error)] = balances.native_balances([(CHAINS["sepolia"], "0x0")])
    assert (balance, error) == (None, "Connection not working")
    assert balances.rpc_problem(RuntimeError("401 Unauthorized for url: https://x/SECRET/")) == "Access refused: check the RPC URL"
    assert balances.rpc_problem(RuntimeError("429 Too Many Requests")) == "Rate limited: try again shortly"
    assert balances.rpc_problem(ValueError("execution reverted")) == "Could not read"


def test_tron_url_drops_jsonrpc_suffix():
    from api.tron import tron_http_url

    assert tron_http_url("https://x.tron-nile.quiknode.pro/abc/jsonrpc") == "https://x.tron-nile.quiknode.pro/abc/"
    assert tron_http_url("https://nile.trongrid.io") == "https://nile.trongrid.io/"
