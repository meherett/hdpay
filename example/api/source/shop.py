"""
The shop: places orders through HDPay and keeps the customer informed by email.

Products are HDPay's project products with a price: paid in USDT or USDC, at that price. A real shop
would buy for its signed-in user and credit their account on deposit.confirmed; here the buyer is picked
from the shop's users, and crediting is a paid_at date on the order.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from . import emails
from .config import Settings
from .hdpay import HDPay, HDPayError
from .mailer import Mailer
from .store import Store

log = logging.getLogger("example.shop")

# what the customer's page shows; HDPay's internal state, sweeps and amounts against the price stay here
PUBLIC_FIELDS = (
    "id", "account_id", "product", "product_name", "chain", "chain_name", "asset", "standard", "decimals",
    "address", "address_url", "status", "expected_amount", "received_amount", "tx_hash", "tx_url",
    "confirmations", "created_at", "expires_at", "expires_in_seconds", "discount",
)


def public(deposit: dict[str, Any]) -> dict[str, Any]:
    return {key: deposit.get(key) for key in PUBLIC_FIELDS}


class Shop:
    def __init__(self, settings: Settings, hdpay: HDPay, store: Store, mailer: Mailer):
        self.settings = settings
        self.hdpay = hdpay
        self.store = store
        self.mailer = mailer

    def products(self) -> list[dict[str, Any]]:
        """What can be bought: a product without a price is not for sale yet."""
        return [p for p in self.hdpay.products() if p.get("price")]

    def buy(
        self,
        account_id: str,
        product: str,
        chain: str,
        asset: str,
        discount: dict[str, Any] | None = None,
        buyer: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """The user's address for the product, at its price; HDPay sets the amount. A discount
        ({percent, ends_at}) is this customer's: HDPay keeps it with the address, and once it ends
        anything still owed is owed at the full price. A custom `buyer` ({account_id, name, email}) is
        used as given, for this order only; only the order keeps their email, for its emails."""
        user = buyer or self.store.user(account_id)
        if user is None:
            raise HDPayError(404, "No such user")
        if not any(p["value"] == product for p in self.products()):
            raise HDPayError(422, "That product is not for sale")
        body = {"account_id": account_id, "product": product, "chain": chain, "asset": asset, "expected_amount": None}
        if discount:
            body |= {"discount_percent": discount["percent"], "discount_ends_at": discount["ends_at"]}
        deposit, new = self.place(user["email"], body)
        return user, deposit, new

    def place(self, email: str, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Opens the deposit; True when it is new to the shop, so the customer gets its address by email."""
        deposit = self.hdpay.open_deposit(body)
        new = self.store.add_order(deposit["id"], email, deposit["account_id"], deposit.get("product"))
        return deposit, new

    def find(self, deposit_id: str) -> dict[str, Any]:
        """Only the shop's own orders: a deposit id from anywhere else is not found."""
        if self.store.order(deposit_id) is None:
            raise HDPayError(404, "No such order")
        return self.hdpay.deposit(deposit_id)

    def renew(self, deposit_id: str) -> tuple[dict[str, Any], bool]:
        """A fresh address for an expired order: same account, product, network and asset, same email,
        and the same discount while it still runs."""
        order = self.store.order(deposit_id)
        if order is None:
            raise HDPayError(404, "No such order")
        old = self.hdpay.deposit(deposit_id)
        priced = any(p["value"] == old.get("product") for p in self.products())
        body = {
            "account_id": old["account_id"],
            "product": old.get("product"),
            "chain": old["chain"],
            "asset": old["asset"],
            # a priced product's amount follows its price, as it is now
            "expected_amount": None if priced else old.get("expected_amount"),
        }
        discount = old.get("discount")
        if priced and discount and datetime.fromisoformat(discount["ends_at"].replace("Z", "+00:00")) > datetime.now(UTC):
            body |= {"discount_percent": discount["percent"], "discount_ends_at": discount["ends_at"]}
        return self.place(order["email"], body)

    def cancel(self, deposit_id: str) -> dict[str, Any]:
        """The customer gave up on this address, to pay another way: HDPay cancels it while it is pending."""
        if self.store.order(deposit_id) is None:
            raise HDPayError(404, "No such order")
        return self.hdpay.cancel(deposit_id)

    def send_address(self, email: str, deposit: dict[str, Any]) -> None:
        try:
            self.mailer.send(email, *emails.created(deposit, self.settings))
        except Exception:
            # the address is on the page anyway; a lost email is not worth failing the order
            log.exception("Could not email the address of %s to %s", deposit["id"], email)

    def handle(self, event: dict[str, Any]) -> None:
        """One HDPay event. Raises if an email fails, so the event is not acknowledged and comes again."""
        if self.store.handled(event["id"]):
            return
        deposit = event["deposit"]
        order = self.store.order(deposit["id"])
        if order is not None:
            if event["type"] == "deposit.confirmed":
                self.store.mark_paid(deposit["id"])   # a real shop credits the account here
            message = emails.for_event(event["type"], deposit, self.settings)
            if message:
                self.mailer.send(order["email"], *message)
        self.store.remember(event["id"])
        log.info("Handled %s #%s (%s)", event["type"], event["seq"], deposit["id"])
