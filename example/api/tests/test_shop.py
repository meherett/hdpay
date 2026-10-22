import json

import httpx
import pytest
from fastapi.testclient import TestClient

from source.config import Settings
from source.hdpay import HDPay
from source.main import create_app
from source.users import hash_password

PRODUCTS = [
    {"value": "10k", "name": "$10,000", "subname": None, "price": "99"},
    {"value": "custom", "name": "Custom", "subname": None, "price": None},
]


def deposit(**changes):
    d = {
        "id": "dep-1", "account_id": "42", "product": "10k", "product_name": "$10,000",
        "chain": "tron-nile", "chain_name": "Tron Nile", "asset": "USDT", "standard": "TRC20", "decimals": 6,
        "address": "TAddress", "address_url": "https://nile/address", "status": "pending",
        "expected_amount": "99000000", "received_amount": "0", "tx_hash": None, "tx_url": None,
        "confirmations": 0, "created_at": "2026-10-21T10:00:00Z", "expires_at": "2026-10-21T10:30:00Z",
        "expires_in_seconds": 1800,
        # HDPay's own, for the shop only
        "internal_status": "pending", "difference": None, "late": False, "confirmed_at": None,
        "swept_at": None, "sweep_tx_hash": None,
    }
    return d | changes


class FakeHDPay:
    """HDPay's gateway, as far as the shop uses it."""

    def __init__(self):
        self.deposits = {}
        self.opened = []
        self.next_id = 1

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer hdp_sk_test"
        path = request.url.path
        if path == "/v1/products":
            return httpx.Response(200, json=PRODUCTS)
        if path == "/v1/chains":
            return httpx.Response(200, json=[{"key": "tron-nile", "name": "Tron Nile", "assets": []}])
        if path == "/v1/deposits" and request.method == "POST":
            body = json.loads(request.content)
            self.opened.append(body)
            if body["asset"] == "TRX":
                return httpx.Response(422, json={"detail": "$10,000 costs $99: pay it in USDT or USDC"})
            for d in self.deposits.values():   # the account's open one
                if d["status"] == "pending" and (d["account_id"], d["product"], d["chain"], d["asset"]) == (
                    body["account_id"], body["product"], body["chain"], body["asset"]
                ):
                    return httpx.Response(200, json=d)
            d = deposit(id=f"dep-{self.next_id}", account_id=body["account_id"], product=body["product"],
                        chain=body["chain"], asset=body["asset"], expected_amount=body["expected_amount"] or "99000000")
            self.next_id += 1
            self.deposits[d["id"]] = d
            return httpx.Response(200, json=d)
        if path.startswith("/v1/deposits/"):
            d = self.deposits.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=d) if d else httpx.Response(404, json={"detail": "Deposit not found"})
        return httpx.Response(404, json={"detail": "Not Found"})


@pytest.fixture
def fake():
    return FakeHDPay()


@pytest.fixture
def client(tmp_path, fake):
    settings = Settings(_env_file=None, hdpay_secret_key="hdp_sk_test", database=tmp_path / "shop.db")
    hdpay = HDPay("http://hdpay.test", "hdp_sk_test", transport=httpx.MockTransport(fake))
    app = create_app(settings, hdpay, follow_events=False)
    app.state.shop.store.add_user("42", "Ada Lovelace", "ada@example.com", hash_password("analytical"))
    with TestClient(app) as c:
        yield c


def shop(client):
    return client.app.state.shop


ORDER = {"account_id": "42", "product": "10k", "chain": "tron-nile", "asset": "USDT"}


def test_lists_come_from_hdpay(client):
    assert client.get("/products").json() == PRODUCTS[:1]   # priced only: the rest is not for sale
    assert client.get("/users").json() == [{"account_id": "42", "name": "Ada Lovelace", "email": "ada@example.com"}]
    assert client.get("/chains").json()[0]["key"] == "tron-nile"


def test_an_order_opens_a_deposit_and_emails_its_address(client, fake):
    response = client.post("/orders", json=ORDER)
    assert response.status_code == 200
    body = response.json()
    assert body["address"] == "TAddress"
    assert "internal_status" not in body and "sweep_tx_hash" not in body
    assert fake.opened == [{"account_id": "42", "product": "10k", "chain": "tron-nile", "asset": "USDT", "expected_amount": None}]

    [mail] = shop(client).mailer.outbox
    assert mail["To"] == "ada@example.com"
    assert "Send exactly 99 USDT (TRC20) on Tron Nile" in mail.get_content()
    assert "http://localhost:4400/pay/dep-1" in mail.get_content()

    # the same order again gets the same open address, and no second email
    assert client.post("/orders", json=ORDER).json()["id"] == "dep-1"
    assert len(shop(client).mailer.outbox) == 1


def test_hdpay_refusals_reach_the_page(client):
    response = client.post("/orders", json=ORDER | {"asset": "TRX"})
    assert response.status_code == 422
    assert response.json()["detail"] == "$10,000 costs $99: pay it in USDT or USDC"

    response = client.post("/orders", json=ORDER | {"account_id": "7"})
    assert (response.status_code, response.json()["detail"]) == (404, "No such user")
    response = client.post("/orders", json=ORDER | {"product": "custom"})
    assert (response.status_code, response.json()["detail"]) == (422, "That product is not for sale")


def test_only_the_shops_orders_can_be_read(client, fake):
    fake.deposits["elsewhere"] = deposit(id="elsewhere", account_id="7")
    assert client.get("/orders/elsewhere").status_code == 404
    client.post("/orders", json=ORDER)
    assert client.get("/orders/dep-1").json()["status"] == "pending"


def test_events_email_the_customer_once(client):
    client.post("/orders", json=ORDER)
    s = shop(client)
    event = {"id": "evt_1", "seq": 1, "type": "deposit.confirmed",
             "deposit": deposit(status="paid", received_amount="99000000", tx_url="https://nile/tx")}
    s.handle(event)
    s.handle(event)   # delivered twice after a reconnect
    mails = s.mailer.outbox[1:]
    assert [m["Subject"] for m in mails] == ["Payment received for $10,000"]
    assert "received 99 USDT" in mails[0].get_content()
    assert s.store.order("dep-1")["paid_at"] is not None


def test_partial_payment_email_says_what_is_left(client):
    client.post("/orders", json=ORDER)
    s = shop(client)
    s.handle({"id": "evt_2", "seq": 2, "type": "deposit.partial",
              "deposit": deposit(status="partial", received_amount="40500000")})
    assert "Send the remaining 58.5 USDT (TRC20)" in s.mailer.outbox[-1].get_content()


def test_events_for_other_deposits_and_sweeps_send_nothing(client):
    client.post("/orders", json=ORDER)
    s = shop(client)
    s.handle({"id": "evt_3", "seq": 3, "type": "deposit.confirmed", "deposit": deposit(id="not-ours")})
    s.handle({"id": "evt_4", "seq": 4, "type": "deposit.swept", "deposit": deposit()})
    assert len(s.mailer.outbox) == 1   # only the address
    assert s.store.handled("evt_3") and s.store.handled("evt_4")


def test_renew_keeps_the_email_and_follows_the_price(client, fake):
    client.post("/orders", json=ORDER)
    fake.deposits["dep-1"]["status"] = "expired"
    fresh = client.post("/orders/dep-1/renew").json()
    assert fresh["id"] == "dep-2"
    assert fake.opened[-1]["expected_amount"] is None   # priced: HDPay sets it
    assert shop(client).store.order("dep-2")["email"] == "ada@example.com"
    assert shop(client).mailer.outbox[-1]["To"] == "ada@example.com"


def test_without_a_key_the_shop_says_so(tmp_path):
    settings = Settings(_env_file=None, hdpay_secret_key="", database=tmp_path / "shop.db")
    with TestClient(create_app(settings, follow_events=False)) as c:
        response = c.get("/products")
    assert response.status_code == 503
    assert "HDPAY_SECRET_KEY" in response.json()["detail"]


DISCOUNT = {"percent": 20, "ends_at": "2026-10-31T18:00:00Z", "full_amount": "125000000", "amount": "99000000"}


def test_the_address_email_names_a_running_discount(client, fake):
    fake.deposits["dep-1"] = deposit(discount=DISCOUNT | {"status": "active"})
    s = shop(client)
    s.send_address("ada@example.com", fake.deposits["dep-1"])
    body = s.mailer.outbox[-1].get_content()
    assert "20% off until Oct 31, 2026 18:00 UTC" in body and "full price of 125 USDT" in body


def test_a_discount_that_ended_unpaid_asks_for_the_rest_of_the_full_price(client):
    client.post("/orders", json=ORDER)
    s = shop(client)
    lapsed = deposit(expected_amount="125000000", received_amount="50000000", discount=DISCOUNT | {"status": "lapsed"})
    s.handle({"id": "evt_9", "seq": 9, "type": "deposit.discount_expired", "deposit": lapsed})
    mail = s.mailer.outbox[-1]
    assert mail["Subject"] == "The discount on $10,000 ended"
    assert "full price of 125 USDT is due" in mail.get_content() and "send 75 USDT (TRC20)" in mail.get_content()


def test_a_customers_discount_goes_to_hdpay_with_the_order(client, fake):
    order = ORDER | {"discount_percent": 20, "discount_ends_at": "2099-01-01T18:00:00Z"}
    assert client.post("/orders", json=order).status_code == 200
    sent = fake.opened[-1]
    assert (sent["discount_percent"], sent["discount_ends_at"]) == (20, "2099-01-01T18:00:00+00:00")
    assert client.post("/orders", json=ORDER | {"discount_percent": 20}).status_code == 422   # no end


@pytest.mark.parametrize("ends_at, carried", [("2099-01-01T18:00:00Z", True), ("2020-01-01T18:00:00Z", False)])
def test_renewing_keeps_a_discount_only_while_it_runs(client, fake, ends_at, carried):
    client.post("/orders", json=ORDER)
    fake.deposits["dep-1"] |= {"status": "expired", "discount": DISCOUNT | {"ends_at": ends_at, "status": "active"}}
    client.post("/orders/dep-1/renew")
    assert ("discount_percent" in fake.opened[-1]) is carried


def test_an_unknown_field_is_refused_not_dropped(client):
    reply = client.post("/orders", json=ORDER | {"coupon": "X"})
    assert reply.status_code == 422 and "coupon" in reply.text


def test_the_payment_page_gets_the_discount(client, fake):
    client.post("/orders", json=ORDER)
    fake.deposits["dep-1"]["discount"] = DISCOUNT | {"status": "active"}
    assert client.get("/orders/dep-1").json()["discount"]["percent"] == 20
