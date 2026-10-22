import pytest
from hdwallet.cryptocurrencies import Ethereum, Tron
from starlette.websockets import WebSocketDisconnect

from factories import NUMBER, key_headers, make_project, today, wallet_address
from api.chains import Family
from api.keys import full_path

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
    path = full_path(NUMBER, Family.TRON, today(), 42, 0)
    assert d["address"] == wallet_address(Tron, path)
    assert (d["status"], d["internal_status"], d["expected_amount"], d["received_amount"]) == ("pending", "pending", "25000000", "0")
    assert (d["product"], d["product_name"], d["standard"]) == ("100k", "$100,000", "TRC20")
    assert "path" not in d and "email" not in d


def test_the_open_deposit_is_reused_and_a_new_product_gets_its_own(client, secret):
    first = create(client, secret).json()
    assert create(client, secret).json()["id"] == first["id"]
    other = create(client, secret, product="10k").json()
    assert other["id"] != first["id"]


def test_indexes_are_per_account_and_per_family(client, secret):
    a0 = create(client, secret, account_id="7").json()
    a1 = create(client, secret, account_id="7", asset="TRX").json()            # same family, next index
    b0 = create(client, secret, account_id="8").json()
    evm0 = create(client, secret, account_id="7", chain="sepolia", asset="ETH").json()   # its own counter
    assert a0["address"] == wallet_address(Tron, full_path(NUMBER, Family.TRON, today(), 7, 0))
    assert a1["address"] == wallet_address(Tron, full_path(NUMBER, Family.TRON, today(), 7, 1))
    assert b0["address"] == wallet_address(Tron, full_path(NUMBER, Family.TRON, today(), 8, 0))
    assert evm0["address"] == wallet_address(Ethereum, full_path(NUMBER, Family.EVM, today(), 7, 0))


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
