from datetime import timedelta

import pytest

from factories import NUMBER, key_headers, make_project, make_user, sign_in
from api import discounts
from api.db import Deposit, Status, find_deposit, get_db, utcnow
from stubs import StubScanner
from watcher import core

CONFIRMATIONS = 20   # tron-nile


# ---- the price

@pytest.mark.parametrize("price, percent, final", [
    ("100", 20, "80.00"),
    ("99.99", 15, "84.99"),     # 84.9915
    ("10.01", 50, "5.01"),      # 5.005, half up
    ("1", 99, "0.01"),
])
def test_the_discounted_price_is_rounded_half_up_to_the_cent(price, percent, final):
    assert discounts.discounted_price(price, percent) == final


# ---- opening a deposit with a discount

PRICED = [{"value": "100k", "name": "$100,000", "subname": None, "price": "100"},
          {"value": "free", "name": "Any amount", "subname": None}]


def body(**changes):
    return {"account_id": "42", "chain": "tron", "asset": "USDT", "product": "100k",
            "discount_percent": 20, "discount_ends_at": (utcnow() + timedelta(days=1)).isoformat() + "Z"} | changes


def open_deposit(client, mongo, project, **changes):
    reply = client.post("/v1/deposits", headers=key_headers(mongo, project), json=body(**changes))
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_a_discount_given_with_the_deposit_sets_its_amount_and_stays_with_it(client, mongo):
    project = make_project(mongo, products=PRICED)
    d = open_deposit(client, mongo, project)
    assert d["expected_amount"] == "80000000"
    assert d["discount"] | {"ends_at": None} == {
        "percent": 20, "ends_at": None, "full_amount": "100000000", "amount": "80000000", "status": "active",
    }
    # the payer's own page shows it too, and the product itself has no discount
    assert client.get(f"/deposits/{d['id']}").json()["discount"]["percent"] == 20
    assert "discount" not in client.get("/v1/products", headers=key_headers(mongo, project)).json()[0]


def test_without_a_discount_it_is_the_full_price(client, mongo):
    project = make_project(mongo, products=PRICED)
    d = open_deposit(client, mongo, project, discount_percent=None, discount_ends_at=None)
    assert (d["expected_amount"], d["discount"]) == ("100000000", None)


def test_a_different_discount_gets_its_own_address(client, mongo):
    project = make_project(mongo, products=PRICED)
    plain = open_deposit(client, mongo, project, discount_percent=None, discount_ends_at=None)
    twenty = open_deposit(client, mongo, project)
    assert twenty["id"] != plain["id"]
    assert open_deposit(client, mongo, project, discount_ends_at=twenty["discount"]["ends_at"])["id"] == twenty["id"]
    assert open_deposit(client, mongo, project, discount_percent=None, discount_ends_at=None)["id"] == plain["id"]
    assert open_deposit(client, mongo, project, discount_percent=10)["id"] not in (plain["id"], twenty["id"])


@pytest.mark.parametrize("changes, error", [
    ({"discount_ends_at": None}, "both discount_percent and discount_ends_at"),
    ({"discount_percent": None}, "both discount_percent and discount_ends_at"),
    ({"discount_percent": 0}, "greater than or equal to 1"),
    ({"discount_percent": 100}, "less than or equal to 99"),
    ({"discount_ends_at": "2020-01-01T00:00:00Z"}, "must be in the future"),
    ({"product": "free"}, "give a product that has a price"),
])
def test_a_discount_is_a_percent_with_a_future_end_on_a_priced_product(client, mongo, changes, error):
    project = make_project(mongo, products=PRICED)
    reply = client.post("/v1/deposits", headers=key_headers(mongo, project), json=body(**changes))
    assert reply.status_code == 422 and error in reply.text


def test_the_end_is_stored_in_utc():
    from api.schemas import CreateDeposit

    d = CreateDeposit(**body(discount_ends_at="2099-01-01T03:00:00+03:00"))
    assert d.discount_ends_at.isoformat() == "2099-01-01T00:00:00"


# ---- the watcher

@pytest.fixture
def chain(mongo):
    make_project(mongo)
    s = StubScanner()
    core.run_once(get_db(), s, 1)    # anchors the cursor at head - 1
    return s


def discounted_deposit(ends_at, address="TDeposit0"):
    d = Deposit.new(
        project_id=get_db().projects.find_one()["_id"], project_number=NUMBER, account_id="1", product="100k",
        chain="tron-nile", family="tron", asset="USDT", address=address,
        derivation_date=utcnow().date().isoformat(), index=0, path="m/1'/195'/1/2026/10/21/0",
        expected_amount="80000000", expires_at=utcnow() + timedelta(minutes=30),
        discount={"percent": 20, "ends_at": ends_at, "full_amount": "100000000", "amount": "80000000",
                  "paid_in_time": "0", "status": "active"},
    )
    d.insert(get_db())
    return d.id


def pay(chain, amount, tx, at):
    """A payment in the next block, made at `at`."""
    block = chain.head + 1
    chain.times[block] = at
    chain.pay("TDeposit0", "USDT", amount, tx, block=block)


def advance(chain, blocks, at=None):
    for _ in range(blocks):
        chain.head += 1
        if at is not None:
            chain.times.setdefault(chain.head, at)   # a payment block keeps its own time
        core.run_once(get_db(), chain, 1)


def get(deposit_id):
    return find_deposit(get_db(), deposit_id)


def event_types():
    return [e["type"] for e in get_db().events.find().sort("seq", 1)]


def test_paid_in_full_before_the_end_keeps_the_discount(chain):
    now = utcnow()
    d = discounted_deposit(ends_at=now + timedelta(hours=1))
    pay(chain, 80_000_000, "tx1", at=now)
    advance(chain, CONFIRMATIONS, at=now)
    got = get(d)
    assert (got.status, got.expected_amount, got.discount["status"]) == (Status.CONFIRMED, "80000000", "kept")


def test_a_payment_made_in_time_counts_even_when_seen_after_the_end(chain):
    now = utcnow()
    d = discounted_deposit(ends_at=now - timedelta(minutes=1))     # ended a minute ago
    pay(chain, 80_000_000, "tx1", at=now - timedelta(minutes=2))   # but was sent before it ended
    advance(chain, 1, at=now)
    got = get(d)   # the scan has reached only the payment's block, from before the end: nothing settled yet
    assert (got.expected_amount, got.discount["paid_in_time"], got.discount["status"]) == ("80000000", "80000000", "active")
    advance(chain, CONFIRMATIONS, at=now)
    got = get(d)
    assert (got.status, got.expected_amount, got.discount["status"]) == (Status.CONFIRMED, "80000000", "kept")
    assert "deposit.discount_expired" not in event_types()


def test_short_when_the_discount_ends_means_the_full_price_is_due(chain):
    now = utcnow()
    d = discounted_deposit(ends_at=now + timedelta(minutes=5))
    pay(chain, 50_000_000, "tx1", at=now)                          # 50 of 80
    advance(chain, CONFIRMATIONS, at=now)
    assert get(d).status == Status.PARTIAL

    later = now + timedelta(minutes=10)
    get_db().deposits.update_one({"_id": d}, {"$set": {"discount.ends_at": utcnow() - timedelta(seconds=1)}})
    advance(chain, 1, at=later)
    got = get(d)
    assert (got.expected_amount, got.discount["status"]) == ("100000000", "lapsed")
    assert event_types()[-1] == "deposit.discount_expired"
    expired = get_db().events.find_one({"type": "deposit.discount_expired"})["deposit"]
    assert (expired["expected_amount"], expired["discount"]["status"]) == ("100000000", "lapsed")

    pay(chain, 30_000_000, "tx2", at=later)                        # the discounted rest is not enough now
    advance(chain, CONFIRMATIONS, at=later)
    assert get(d).status == Status.PARTIAL
    pay(chain, 20_000_000, "tx3", at=later)                        # 100 in all
    advance(chain, CONFIRMATIONS, at=later)
    assert get(d).status == Status.CONFIRMED


def test_nothing_paid_when_the_discount_ends_keeps_the_discounted_amount(chain):
    d = discounted_deposit(ends_at=utcnow() - timedelta(seconds=1))
    advance(chain, 1, at=utcnow())
    got = get(d)
    assert (got.status, got.expected_amount, got.discount["status"]) == (Status.PENDING, "80000000", "active")
    assert "deposit.discount_expired" not in event_types()


def test_a_first_payment_after_the_end_lapses_it(chain):
    now = utcnow()
    d = discounted_deposit(ends_at=now - timedelta(minutes=5))
    pay(chain, 80_000_000, "tx1", at=now)            # the discounted amount, but too late
    advance(chain, 1, at=now)
    got = get(d)
    assert (got.expected_amount, got.discount["status"]) == ("100000000", "lapsed")
    advance(chain, CONFIRMATIONS, at=now)
    assert get(d).status == Status.PARTIAL           # 80 of the 100 now due


def test_the_discount_waits_for_the_scan_to_pass_its_end(chain):
    now = utcnow()
    d = discounted_deposit(ends_at=now - timedelta(minutes=1))
    advance(chain, 1, at=now - timedelta(minutes=5))   # the scan is still behind the end
    assert get(d).discount["status"] == "active"


# ---- support

@pytest.fixture
def admin(client, mongo):
    make_user(mongo)
    return sign_in(client)


def in_review(client, mongo, ended=True):
    project = make_project(mongo, products=PRICED)
    d = open_deposit(client, mongo, project)
    ends_at = utcnow() - timedelta(hours=1) if ended else utcnow() + timedelta(hours=1)
    get_db().deposits.update_one({"_id": d["id"]}, {"$set": {
        "status": Status.REVIEW.value, "received_amount": "50000000", "discount.ends_at": ends_at,
    }})
    return f"/admin/deposits/{d['id']}/reopen"


def test_reopening_after_the_end_asks_for_the_full_price(client, mongo, admin):
    reply = client.post(in_review(client, mongo), headers=admin).json()
    assert (reply["expected_amount"], reply["discount"]["status"]) == ("100000000", "lapsed")


def test_support_can_keep_the_discount(client, mongo, admin):
    reply = client.post(in_review(client, mongo), headers=admin, json={"keep_discount": True}).json()
    assert (reply["expected_amount"], reply["discount"]["status"]) == ("80000000", "honored")


def test_reopening_before_the_end_keeps_it_running(client, mongo, admin):
    reply = client.post(in_review(client, mongo, ended=False), headers=admin).json()
    assert (reply["expected_amount"], reply["discount"]["status"]) == ("80000000", "active")


def test_unpaid_deposits_that_lapsed_get_their_discount_back(mongo):
    from api.discounts import restore_unpaid

    snapshot = {"percent": 70, "ends_at": utcnow(), "full_amount": "80000000", "amount": "24000000", "paid_in_time": "0"}
    mongo.deposits.insert_many([
        {"_id": "unpaid", "address": "TUnpaid", "expected_amount": "80000000", "received_amount": "0", "discount": snapshot | {"status": "lapsed"}},
        {"_id": "short", "address": "TShort", "expected_amount": "80000000", "received_amount": "1000000", "discount": snapshot | {"status": "lapsed"}},
    ])
    assert restore_unpaid(mongo) == 1
    unpaid = mongo.deposits.find_one({"_id": "unpaid"})
    assert (unpaid["expected_amount"], unpaid["discount"]["status"]) == ("24000000", "active")
    assert mongo.deposits.find_one({"_id": "short"})["expected_amount"] == "80000000"   # it did pay something



def test_reopening_with_another_product_asks_for_its_price(client, mongo, admin):
    products = PRICED + [{"value": "50k", "name": "$50,000", "subname": None, "price": "60"}]
    project = make_project(mongo, products=products)
    d = open_deposit(client, mongo, project)      # 100k at 20% off: 80
    get_db().deposits.update_one({"_id": d["id"]}, {"$set": {"status": Status.REVIEW.value, "received_amount": "10000000"}})
    reply = client.post(f"/admin/deposits/{d['id']}/reopen", headers=admin, json={"product": "50k"}).json()
    # 60 at the same 20% off is 48, while the discount runs
    assert (reply["product"], reply["expected_amount"]) == ("50k", "48000000")
    assert (reply["discount"]["full_amount"], reply["discount"]["amount"]) == ("60000000", "48000000")
