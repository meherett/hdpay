from datetime import timedelta

import pytest
from hdwallet.cryptocurrencies import Ethereum, Tron
from starlette.websockets import WebSocketDisconnect

from factories import NUMBER, key_headers, make_project, wallet_address
from api.chains import Family
from api.db import get_db, utcnow
from api.keys import full_path, moment_of

PRODUCTS = [{"value": "100k", "name": "$100,000", "subname": "(Most popular)"}, {"value": "10k", "name": "$10,000", "subname": None}]


@pytest.fixture
def project(mongo):
    return make_project(mongo, products=PRODUCTS)


@pytest.fixture
def secret(mongo, project):
    return key_headers(mongo, project)


def create(client, headers, **body):
    return client.post("/v1/deposits", headers=headers, json={"account_id": "42", "chain": "tron", "asset": "USDT", "product": "100k", **body})


def test_a_deposit_is_derived_under_the_project(client, secret):
    reply = create(client, secret, expected_amount=25_000_000)
    assert reply.status_code == 200
    d = reply.json()
    stored = get_db().deposits.find_one({"_id": d["id"]})
    seconds, microseconds = map(int, stored["path"].split("/")[-2:])
    assert stored["path"] == full_path(NUMBER, Family.TRON, 42, moment_of(seconds, microseconds))
    assert d["address"] == wallet_address(Tron, stored["path"])
    assert stored["derived_at"] == moment_of(seconds, microseconds).isoformat(timespec="microseconds") + "Z"
    assert (d["status"], d["internal_status"], d["expected_amount"], d["received_amount"]) == ("pending", "pending", "25000000", "0")
    assert (d["product"], d["product_name"], d["standard"]) == ("100k", "$100,000", "TRC20")
    assert "path" not in d and "email" not in d


def test_the_open_deposit_is_reused_and_a_new_product_gets_its_own(client, secret):
    first = create(client, secret).json()
    assert create(client, secret).json()["id"] == first["id"]
    other = create(client, secret, product="10k").json()
    assert other["id"] != first["id"]


def test_every_deposit_gets_its_own_moment_and_address(client, secret):
    made = [
        create(client, secret, account_id="7").json(),
        create(client, secret, account_id="7", asset="TRX").json(),
        create(client, secret, account_id="8").json(),
        create(client, secret, account_id="7", chain="sepolia", asset="ETH").json(),
    ]
    stored = [get_db().deposits.find_one({"_id": d["id"]}) for d in made]
    assert len({d["address"] for d in made}) == 4 and len({s["path"] for s in stored}) == 4
    for d, s in zip(made, stored):
        coin = Ethereum if s["family"] == "evm" else Tron
        assert d["address"] == wallet_address(coin, s["path"])


def test_the_same_microsecond_takes_the_next_one(client, secret, monkeypatch):
    import api.deposits as deposits
    from datetime import datetime as real

    frozen = real(2026, 10, 25, 13, 5, 22, 123456)

    class Clock(real):
        @classmethod
        def now(cls, tz=None):
            return frozen.replace(tzinfo=tz)

    monkeypatch.setattr(deposits, "datetime", Clock)
    first = create(client, secret, account_id="7").json()
    second = create(client, secret, account_id="7", product="10k").json()   # same account, same microsecond
    paths = [get_db().deposits.find_one({"_id": d["id"]})["path"] for d in (first, second)]
    assert paths == [f"m/{NUMBER}'/195'/7/25707922/123456", f"m/{NUMBER}'/195'/7/25707922/123457"]
    assert first["address"] != second["address"]


def test_projects_share_nothing(client, mongo, project, secret):
    other = make_project(mongo, number=NUMBER + 1, products=PRODUCTS)
    mine = create(client, secret).json()
    theirs = create(client, key_headers(mongo, other)).json()
    assert mine["address"] != theirs["address"]
    assert client.get(f"/v1/deposits/{theirs['id']}", headers=secret).status_code == 404


def test_account_ids_are_canonical(client, secret):
    assert create(client, secret, account_id="0042").json()["account_id"] == "42"


@pytest.mark.parametrize("account_id", ["alice", "-1", "1.5", "", str(2**31)])
def test_account_id_must_fit_in_the_path(client, secret, account_id):
    assert create(client, secret, account_id=account_id).status_code == 422


@pytest.mark.parametrize("chain, asset, problem", [
    ("solana", "USDT", "does not accept 'solana'"),
    ("ethereum", "ETH", "does not accept 'ethereum'"),      # in the chains file, not in the project
    ("tron", "USDC", "accepts TRX, USDT on Tron"),
])
def test_only_the_projects_networks_and_assets(client, secret, chain, asset, problem):
    reply = create(client, secret, chain=chain, asset=asset)
    assert reply.status_code == 422 and problem in reply.text


def test_products_must_be_the_projects(client, mongo, secret):
    assert create(client, secret, product="gold").status_code == 422
    assert create(client, secret, product=None).status_code == 422
    bare = make_project(mongo, number=NUMBER + 2)
    assert create(client, key_headers(mongo, bare), product="100k").status_code == 422
    assert create(client, key_headers(mongo, bare), product=None).json()["product"] is None


def test_a_priced_product_sets_the_amount_in_a_usd_stablecoin(client, mongo):
    priced = [{"value": "100k", "name": "$100,000", "subname": None, "price": "499"},
              {"value": "10k", "name": "$10,000", "subname": None, "price": "99.50"}]
    project = make_project(mongo, number=NUMBER + 5, products=priced)
    key = key_headers(mongo, project, "publishable")
    tron = create(client, key, product="100k").json()                                   # USDT, 6 decimals
    assert tron["expected_amount"] == "499000000"
    bsc = create(client, key, product="10k", chain="bsc", asset="USDC", account_id="7").json()   # 18 decimals
    assert bsc["expected_amount"] == str(9950 * 10**16)
    native = create(client, key, product="100k", asset="TRX", account_id="8")
    assert native.status_code == 422 and "pay it in USDT or USDC" in native.text
    cheaper = create(client, key, product="100k", account_id="9", expected_amount=1)
    assert cheaper.status_code == 422 and "it follows the price" in cheaper.text
    assert create(client, key, product="100k", account_id="9", expected_amount=499_000_000).status_code == 200
    listed = client.get("/v1/products", headers=key).json()
    assert [p["price"] for p in listed] == ["499", "99.50"]
    usd = {(c["key"], a["symbol"]): a["usd"] for c in client.get("/v1/chains", headers=key).json() for a in c["assets"]}
    assert usd[("tron", "USDT")] and not usd[("tron", "TRX")]


@pytest.mark.parametrize("price", ["0", "-1", "1.234", "abc", "1e3"])
def test_prices_are_positive_usd_amounts(client, mongo, price):
    from api.schemas import CreateProject

    with pytest.raises(ValueError):
        CreateProject(name="x", products=[{"value": "a", "name": "A", "price": price}])


def test_large_amounts_stay_strings(client, secret):
    wei = 10**30
    assert create(client, secret, chain="sepolia", asset="ETH", expected_amount=wei).json()["expected_amount"] == str(wei)


def test_chains_and_products_are_the_projects(client, mongo, project):
    publishable = key_headers(mongo, project, "publishable")
    chains = {c["key"]: [a["symbol"] for a in c["assets"]] for c in client.get("/v1/chains", headers=publishable).json()}
    assert chains == {"tron": ["TRX", "USDT"], "tron-nile": ["TRX", "USDT"], "sepolia": ["ETH", "USDC"], "bsc": ["BNB", "USDT", "USDC"]}
    assert client.get("/v1/products", headers=publishable).json() == [{**p, "price": None} for p in PRODUCTS]


def test_a_publishable_key_cannot_read_events_or_lists(client, mongo, project):
    publishable = key_headers(mongo, project, "publishable")
    assert create(client, publishable).status_code == 200
    assert client.get("/v1/events", headers=publishable).status_code == 403
    assert client.get("/v1/deposits", headers=publishable).status_code == 403


@pytest.mark.parametrize("authorization", [None, "Bearer nope", "Bearer hdp_sk_unknown", "Basic abc"])
def test_a_missing_or_unknown_key_is_refused(client, authorization):
    headers = {"Authorization": authorization} if authorization else {}
    assert client.get("/v1/products", headers=headers).status_code == 401


def test_the_public_view_has_nothing_internal(client, secret):
    d = create(client, secret).json()
    public = client.get(f"/deposits/{d['id']}").json()
    assert public["id"] == d["id"] and public["product_name"] == "$100,000"
    for internal in ("internal_status", "sweep_tx_hash", "path", "project_id"):
        assert internal not in public


def test_creating_a_deposit_is_an_event_and_acks_move_forward(client, secret):
    d = create(client, secret).json()
    listed = client.get("/v1/events", headers=secret).json()
    assert [(e["seq"], e["type"]) for e in listed["events"]] == [(1, "deposit.created")]
    assert listed["events"][0]["deposit"]["id"] == d["id"] and listed["last_seq"] == 1
    assert client.post("/v1/events/ack", headers=secret, json={"seq": 1}).status_code == 204
    assert client.get("/v1/events", headers=secret).json() == {"events": [], "last_seq": 1}
    client.post("/v1/events/ack", headers=secret, json={"seq": 0})   # refused: seq starts at 1
    assert client.get("/v1/events?after=0", headers=secret).json()["last_seq"] == 1


def test_the_stream_sends_what_was_missed_then_new_events(client, mongo, project, secret):
    first = create(client, secret).json()
    with client.websocket_connect("/v1/events/stream", headers=secret) as ws:
        assert ws.receive_json() == {"type": "ready", "after": 0}
        missed = ws.receive_json()
        assert (missed["seq"], missed["type"], missed["deposit"]["id"]) == (1, "deposit.created", first["id"])
        ws.send_json({"ack": 1})
        second = create(client, secret, product="10k").json()
        live = ws.receive_json()
        assert (live["seq"], live["deposit"]["id"]) == (2, second["id"])
    assert mongo.projects.find_one({"_id": project["_id"]})["events_acked"] >= 1


def test_the_stream_needs_a_secret_key(client, mongo, project):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/v1/events/stream", headers=key_headers(mongo, project, "publishable")):
            pass
    assert closed.value.code == 4403
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/v1/events/stream"):
            pass
    assert closed.value.code == 4401


def test_a_project_lists_its_deposits(client, secret):
    create(client, secret, account_id="1")
    create(client, secret, account_id="2")
    listed = client.get("/v1/deposits", headers=secret, params={"account_id": "2"}).json()
    assert [d["account_id"] for d in listed] == ["2"]


# ---- a project cancels a pending deposit

def cancel(client, headers, deposit_id):
    return client.post(f"/v1/deposits/{deposit_id}/cancel", headers=headers)


def test_a_pending_deposit_is_cancelled(client, mongo, secret):
    d = create(client, secret).json()
    reply = cancel(client, secret, d["id"])
    assert reply.status_code == 200, reply.text
    got = reply.json()
    assert (got["internal_status"], got["status"], got["expires_in_seconds"]) == ("cancelled", "cancelled", 0)
    assert got["cancelled_at"]
    [event] = [e for e in mongo.events.find() if e["type"] == "deposit.cancelled"]
    assert event["deposit"]["id"] == d["id"] and event["deposit"]["cancelled_at"] == got["cancelled_at"]
    # asking again changes nothing: the same deposit, no second event
    again = cancel(client, secret, d["id"])
    assert again.status_code == 200 and again.json()["cancelled_at"] == got["cancelled_at"]
    assert mongo.events.count_documents({"type": "deposit.cancelled"}) == 1
    # the next deposit for the same account, network, coin and product is a new address
    fresh = create(client, secret).json()
    assert fresh["id"] != d["id"] and fresh["address"] != d["address"] and fresh["internal_status"] == "pending"


@pytest.mark.parametrize("status, received", [
    ("detected", "5000000"), ("partial", "5000000"), ("confirmed", "25000000"), ("expired", "0"), ("review", "1"),
    ("swept", "25000000"),
])
def test_only_a_pending_deposit_can_be_cancelled(client, mongo, secret, status, received):
    d = create(client, secret).json()
    mongo.deposits.update_one({"_id": d["id"]}, {"$set": {"status": status, "received_amount": received}})
    reply = cancel(client, secret, d["id"])
    assert reply.status_code == 409 and "only a pending deposit can be cancelled" in reply.text
    assert mongo.deposits.find_one({"_id": d["id"]})["status"] == status   # unchanged


def test_a_pending_deposit_that_received_something_is_not_cancelled(client, mongo, secret):
    d = create(client, secret).json()
    mongo.deposits.update_one({"_id": d["id"]}, {"$set": {"received_amount": "1"}})   # counted, status not saved yet
    assert cancel(client, secret, d["id"]).status_code == 409


def test_cancelling_needs_the_secret_key_of_the_deposits_project(client, mongo, project, secret):
    d = create(client, secret).json()
    publishable = cancel(client, key_headers(mongo, project, kind="publishable"), d["id"])
    assert publishable.status_code == 403 and "secret key" in publishable.text
    other = make_project(mongo, number=NUMBER + 1, products=PRODUCTS)
    assert cancel(client, key_headers(mongo, other), d["id"]).status_code == 404
    assert mongo.deposits.find_one({"_id": d["id"]})["status"] == "pending"


# ---- a project reopens a deposit in review, for the rest of a short payment

def reopen(client, headers, deposit_id, **body):
    return client.post(f"/v1/deposits/{deposit_id}/reopen", headers=headers, json=body or None)


def in_review(mongo, deposit_id, received="10000000"):
    mongo.deposits.update_one({"_id": deposit_id}, {"$set": {
        "status": "review", "received_amount": received, "confirmed_amount": received,
        "expires_at": utcnow() - timedelta(hours=1)}})


def test_a_deposit_in_review_is_reopened_for_the_rest(client, mongo, secret):
    d = create(client, secret, expected_amount=25_000_000).json()
    in_review(mongo, d["id"])
    reply = reopen(client, secret, d["id"], expires_in_seconds=3600)
    assert reply.status_code == 200, reply.text
    got = reply.json()
    assert (got["internal_status"], got["received_amount"], got["expected_amount"]) == ("partial", "10000000", "25000000")
    assert 3590 <= got["expires_in_seconds"] <= 3600
    [event] = [e for e in mongo.events.find() if e["type"] == "deposit.reopened"]
    assert event["deposit"]["id"] == d["id"] and event["deposit"]["internal_status"] == "partial"


def test_without_a_time_it_waits_the_projects_remainder_window(client, mongo, project, secret):
    mongo.projects.update_one({"_id": project["_id"]}, {"$set": {"remainder_window_minutes": 90}})
    d = create(client, secret, expected_amount=25_000_000).json()
    in_review(mongo, d["id"])
    got = reopen(client, secret, d["id"]).json()
    assert 89 * 60 < got["expires_in_seconds"] <= 90 * 60


@pytest.mark.parametrize("status", ["pending", "detected", "partial", "confirmed", "swept", "expired"])
def test_only_a_deposit_in_review_is_reopened(client, mongo, secret, status):
    d = create(client, secret, expected_amount=25_000_000).json()
    mongo.deposits.update_one({"_id": d["id"]}, {"$set": {"status": status}})
    reply = reopen(client, secret, d["id"])
    assert reply.status_code == 409 and "only a deposit in review can be reopened" in reply.text


def test_a_cancelled_deposit_is_not_reopened_a_new_one_is_opened(client, mongo, secret):
    d = create(client, secret, expected_amount=25_000_000).json()
    assert cancel(client, secret, d["id"]).status_code == 200
    reply = reopen(client, secret, d["id"])
    assert reply.status_code == 409 and "open a new deposit instead" in reply.text
    fresh = create(client, secret, expected_amount=25_000_000).json()
    assert fresh["address"] != d["address"] and fresh["internal_status"] == "pending"


@pytest.mark.parametrize("seconds", [59, 7 * 24 * 3600 + 1])
def test_the_new_window_is_a_minute_to_a_week(client, mongo, secret, seconds):
    d = create(client, secret, expected_amount=25_000_000).json()
    in_review(mongo, d["id"])
    assert reopen(client, secret, d["id"], expires_in_seconds=seconds).status_code == 422


def test_reopening_needs_the_secret_key_of_the_deposits_project(client, mongo, project, secret):
    d = create(client, secret, expected_amount=25_000_000).json()
    in_review(mongo, d["id"])
    publishable = reopen(client, key_headers(mongo, project, kind="publishable"), d["id"])
    assert publishable.status_code == 403
    other = make_project(mongo, number=NUMBER + 1, products=PRODUCTS)
    assert reopen(client, key_headers(mongo, other), d["id"]).status_code == 404
    assert mongo.deposits.find_one({"_id": d["id"]})["status"] == "review"


def priced_project(mongo):
    priced = [{"value": "10k", "name": "$10,000", "subname": None, "price": "79"},
              {"value": "25k", "name": "$25,000", "subname": None, "price": "249"},
              {"value": "mini", "name": "Mini", "subname": None, "price": "0.40"},
              {"value": "half", "name": "Half", "subname": None, "price": "0.50"}]
    project = make_project(mongo, number=NUMBER + 7, products=priced)
    return key_headers(mongo, project)


def test_reopening_for_another_product_counts_what_was_sent(client, mongo):
    headers = priced_project(mongo)
    d = client.post("/v1/deposits", headers=headers, json={"account_id": "2009", "chain": "tron", "asset": "USDT", "product": "10k"}).json()
    in_review(mongo, d["id"], received="500000")                              # 0.5 of 79 USDT
    got = reopen(client, headers, d["id"], product="25k").json()
    assert (got["internal_status"], got["product"], got["expected_amount"], got["received_amount"]) == ("partial", "25k", "249000000", "500000")
    assert got["address"] == d["address"]                                      # the same address, the rest is 248.5


@pytest.mark.parametrize("product, difference", [("half", "exact"), ("mini", "over")])
def test_a_product_what_was_sent_already_pays_is_paid_at_once(client, mongo, product, difference):
    headers = priced_project(mongo)
    d = client.post("/v1/deposits", headers=headers, json={"account_id": "2009", "chain": "tron", "asset": "USDT", "product": "10k"}).json()
    in_review(mongo, d["id"], received="500000")                              # 0.5 USDT sent
    reply = reopen(client, headers, d["id"], product=product)
    assert reply.status_code == 200, reply.text
    got = reply.json()
    assert (got["internal_status"], got["status"], got["product"], got["difference"]) == ("confirmed", "paid", product, difference)
    assert got["confirmed_at"]
    types = [e["type"] for e in mongo.events.find().sort("seq", 1)]
    assert types[-1] == "deposit.confirmed" and "deposit.reopened" not in types
    stored = mongo.deposits.find_one({"_id": d["id"]})
    assert stored["confirmed_amount"] == "500000"                             # what the sweeper moves


def test_an_unknown_product_is_refused(client, mongo):
    headers = priced_project(mongo)
    d = client.post("/v1/deposits", headers=headers, json={"account_id": "2009", "chain": "tron", "asset": "USDT", "product": "10k"}).json()
    in_review(mongo, d["id"], received="500000")
    assert reopen(client, headers, d["id"], product="nope").status_code == 422
    assert mongo.deposits.find_one({"_id": d["id"]})["status"] == "review"
