"""
Deposit discounts: a percent off a priced product until a moment, after which it is the full price.

Whoever opens a deposit (the project's backend, for one customer) may give it `discount_percent`
(1 to 99) and `discount_ends_at`. The deposit then expects the discounted price and keeps it all:

    discount = {
        "percent": 20,
        "ends_at": datetime,           # naive UTC
        "full_amount": "100000000",    # the full price, in base units
        "amount": "80000000",          # the discounted price, in base units
        "paid_in_time": "0",           # received in transactions made before ends_at
        "status": "active",            # active -> kept | lapsed; honored when support keeps it after the end
    }

The discount holds for whatever is paid in full, in transactions made (on the chain) before it ends.
A deposit still short of the discounted price when it ends lapses: it expects the full price, and the
customer owes the full price minus what they sent. A deposit that received nothing keeps its discounted
amount (it shows as ended); it lapses only if a payment arrives after the end. Support can still honor the discount on reopen.
"""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

ACTIVE, KEPT, LAPSED, HONORED = "active", "kept", "lapsed", "honored"
CENT = Decimal("0.01")


def discounted_price(price: str, percent: int) -> str:
    """The price less the percent, to the cent, half up: 99.99 at 15% off is 84.99."""
    value = (Decimal(price) * (100 - percent) / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return f"{value:f}"


def snapshot(percent: int, ends_at: datetime, full_amount: int, amount: int) -> dict[str, Any]:
    """What a new deposit keeps of its discount."""
    return {
        "percent": percent,
        "ends_at": ends_at,
        "full_amount": str(full_amount),
        "amount": str(amount),
        "paid_in_time": "0",
        "status": ACTIVE,
    }


def restore_unpaid(db: Any) -> int:
    """Deposits that lapsed to the full price with nothing received go back to their discounted amount
    (an earlier rule lapsed them too). Returns how many."""
    restored = 0
    for doc in db.deposits.find({"discount.status": LAPSED, "received_amount": "0"}):
        db.deposits.update_one(
            {"_id": doc["_id"], "received_amount": "0"},
            {"$set": {"expected_amount": doc["discount"]["amount"], "discount.status": ACTIVE}},
        )
        restored += 1
    return restored


def in_time(discount: dict[str, Any] | None, paid_at: datetime) -> bool:
    """A payment made before the discount ended, to a deposit whose discount is still running."""
    return bool(discount and discount["status"] == ACTIVE and paid_at < discount["ends_at"])


def enough(received: int, expected: int, tolerance_bps: int) -> bool:
    """The same floor the watcher credits by: the expected amount less the project's tolerance."""
    return received >= expected - expected * tolerance_bps // 10_000


def settle(discount: dict[str, Any], tolerance_bps: int) -> tuple[str, str | None]:
    """At the end of a running discount: kept when enough was paid in time, else lapsed.

    Returns the new status and, for a lapse, the amount the deposit now expects (the full price)."""
    if enough(int(discount["paid_in_time"]), int(discount["amount"]), tolerance_bps):
        return KEPT, None
    return LAPSED, discount["full_amount"]
