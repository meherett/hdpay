"""What the customer is told, by email, as their payment moves along."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from .config import Settings


def amount(units: str | None, decimals: int) -> str:
    """Base units as a plain number: '25000000' with 6 decimals is '25'."""
    value = Decimal(units or 0).scaleb(-decimals)
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _asset(d: dict[str, Any]) -> str:
    return f"{d['asset']} ({d['standard']})" if d.get("standard") else d["asset"]


def _when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%b %d, %Y %H:%M UTC")
    except ValueError:
        return iso


def _what(d: dict[str, Any]) -> str:
    return d.get("product_name") or "your order"


def created(d: dict[str, Any], s: Settings) -> tuple[str, str]:
    to_send = (
        f"Send exactly {amount(d['expected_amount'], d['decimals'])} {_asset(d)}"
        if d.get("expected_amount")
        else f"Send any amount of {_asset(d)}"
    )
    offer = ""
    if d.get("discount") and d["discount"]["status"] == "active":
        offer = (
            f"That is {d['discount']['percent']}% off until {_when(d['discount']['ends_at'])}: pay in full before then. "
            f"After it, the full price of {amount(d['discount']['full_amount'], d['decimals'])} {d['asset']} is due.\n\n"
        )
    return (
        f"Your payment address for {_what(d)}",
        f"{to_send} on {d['chain_name']} to:\n\n"
        f"    {d['address']}\n\n"
        f"The address is reserved for you until {_when(d['expires_at'])}.\n"
        f"Follow your payment here: {s.web_url}/pay/{d['id']}\n\n"
        f"{offer}"
        f"Only send {d['asset']} on {d['chain_name']}: anything else sent to this address is lost.\n\n"
        f"{s.shop_name}",
    )


def _detected(d: dict[str, Any], s: Settings) -> tuple[str, str]:
    return (
        f"We see your payment for {_what(d)}",
        f"{amount(d['received_amount'], d['decimals'])} {d['asset']} arrived on {d['chain_name']}. "
        f"We are waiting for the network to confirm it; you will get another email once it is done.\n\n"
        f"{s.web_url}/pay/{d['id']}\n\n{s.shop_name}",
    )


def _partial(d: dict[str, Any], s: Settings) -> tuple[str, str]:
    received = Decimal(d["received_amount"])
    rest = Decimal(d["expected_amount"] or 0) - received
    return (
        f"Part of your payment for {_what(d)} arrived",
        f"We received {amount(str(received), d['decimals'])} of {amount(d['expected_amount'], d['decimals'])} {d['asset']}. "
        f"Send the remaining {amount(str(max(rest, Decimal(0))), d['decimals'])} {_asset(d)} to the same address:\n\n"
        f"    {d['address']}\n\n"
        f"Before {_when(d['expires_at'])}. Follow it here: {s.web_url}/pay/{d['id']}\n\n{s.shop_name}",
    )


def _reopened(d: dict[str, Any], s: Settings) -> tuple[str, str]:
    subject, body = _partial(d, s)
    return f"You can complete your payment for {_what(d)}", body


def _confirmed(d: dict[str, Any], s: Settings) -> tuple[str, str]:
    tx = f"\nTransaction: {d['tx_url']}\n" if d.get("tx_url") else ""
    return (
        f"Payment received for {_what(d)}",
        f"Thank you! We received {amount(d['received_amount'], d['decimals'])} {d['asset']} on {d['chain_name']} "
        f"and your order is paid.\n{tx}\n{s.shop_name}",
    )


def _review(d: dict[str, Any], s: Settings) -> tuple[str, str]:
    return (
        f"Your payment for {_what(d)} needs a look",
        f"We received {amount(d['received_amount'], d['decimals'])} {d['asset']}, less than the "
        f"{amount(d['expected_amount'], d['decimals'])} expected, and the rest did not arrive in time. "
        f"Reply to this email with your payment reference {d['id']} and we will sort it out.\n\n{s.shop_name}",
    )


def _expired(d: dict[str, Any], s: Settings) -> tuple[str, str]:
    return (
        f"Your payment address for {_what(d)} expired",
        f"Nothing arrived at {d['address']} in time, so the address is no longer reserved for you. "
        f"Start again any time: {s.web_url}\n\n"
        f"If you did send a payment, reply with the reference {d['id']}: late payments are still counted.\n\n{s.shop_name}",
    )


def _discount_expired(d: dict[str, Any], s: Settings) -> tuple[str, str]:
    received = Decimal(d["received_amount"])
    rest = max(Decimal(d["expected_amount"]) - received, Decimal(0))
    sent = f"You sent {amount(str(received), d['decimals'])} {d['asset']}, so " if received else ""
    return (
        f"The discount on {_what(d)} ended",
        f"The {d['discount']['percent']}% discount ended before your payment was complete, so the full price of "
        f"{amount(d['expected_amount'], d['decimals'])} {d['asset']} is due. {sent}"
        f"send {amount(str(rest), d['decimals'])} {_asset(d)} to the same address:\n\n"
        f"    {d['address']}\n\n"
        f"Before {_when(d['expires_at'])}. Follow it here: {s.web_url}/pay/{d['id']}\n\n{s.shop_name}",
    )


# deposit.created is answered when the order is placed; deposit.swept is the shop's business only
FOR_EVENT = {
    "deposit.detected": _detected,
    "deposit.partial": _partial,
    "deposit.reopened": _reopened,
    "deposit.confirmed": _confirmed,
    "deposit.review": _review,
    "deposit.expired": _expired,
    "deposit.discount_expired": _discount_expired,
}


def for_event(event_type: str, deposit: dict[str, Any], settings: Settings) -> tuple[str, str] | None:
    """The email for an event, or None when the customer is not told about it."""
    compose = FOR_EVENT.get(event_type)
    return compose(deposit, settings) if compose else None
