"""Opening deposits and recording what happens to them, shared by the gateway, the admin and the workers."""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from . import discounts, events
from .chains import CHAINS
from .db import Deposit, Status, find_deposit, utcnow
from .keys import address_at, full_path
from .projects import USD_STABLECOINS, accepted, get_project, price_amount, setting
from .schemas import CreateDeposit, ProjectDepositOut


def get_deposit_or_404(db: Database, deposit_id: str, project_id: str | None = None) -> Deposit:
    deposit = find_deposit(db, deposit_id)
    if deposit is None or (project_id is not None and deposit.project_id != project_id):
        raise HTTPException(404, "deposit not found")
    return deposit


def notify(db: Database, deposit: Deposit, event_type: str, project: dict[str, Any] | None = None) -> None:
    """Record an event for the deposit's project, with the deposit as the project sees it now."""
    project = project or get_project(db, deposit.project_id)
    snapshot = ProjectDepositOut.from_deposit(deposit, project).model_dump(mode="json")
    events.emit(db, deposit, event_type, snapshot)


def notify_status(db: Database, deposit: Deposit, project: dict[str, Any] | None = None) -> None:
    """The event for the status the deposit just moved to."""
    event_type = events.EVENT_FOR_STATUS.get(deposit.status)
    if event_type:
        notify(db, deposit, event_type, project)


def open_deposit(db: Database, project: dict[str, Any], body: CreateDeposit) -> Deposit:
    """The account's open deposit for this network, asset and product, or a new one on a fresh address."""
    chain = CHAINS.get(body.chain)
    usable = {c.key: assets for c, assets in accepted(project)}
    if chain is None or chain.key not in usable:
        names = ", ".join(usable) or "none yet"
        raise HTTPException(422, f"this project does not accept {body.chain!r}; it accepts {names}")
    if body.asset not in usable[chain.key]:
        raise HTTPException(422, f"this project accepts {', '.join(usable[chain.key])} on {chain.name}, not {body.asset!r}")
    from .chain_sync import refused

    reason = refused(db, chain.key, body.asset)
    if reason:
        raise HTTPException(503, reason)
    listed = [p["value"] for p in project.get("products") or []]
    if listed and body.product not in listed:
        raise HTTPException(422, f"product must be one of {', '.join(listed)}")
    if not listed and body.product is not None:
        raise HTTPException(422, "this project has no products, so leave product out")
    product = next((p for p in project.get("products") or [] if p["value"] == body.product), None)
    now = utcnow()
    discount = None
    if product and product.get("price"):
        # the product's price (less this deposit's discount) sets the amount, so a checkout page cannot change it
        price = product["price"]
        if body.discount_percent:
            price = discounts.discounted_price(product["price"], body.discount_percent)
        if body.asset not in USD_STABLECOINS:
            raise HTTPException(422, f"{product['name']} costs ${price}: pay it in {' or '.join(USD_STABLECOINS)}")
        expected = price_amount(price, chain.decimals(body.asset))
        if body.expected_amount is not None and body.expected_amount != expected:
            raise HTTPException(422, f"{product['name']} costs ${price}: leave expected_amount out, it follows the price")
        body = body.model_copy(update={"expected_amount": expected})
        if body.discount_percent:
            full = price_amount(product["price"], chain.decimals(body.asset))
            discount = discounts.snapshot(body.discount_percent, body.discount_ends_at, full, expected)
    elif body.discount_percent:
        raise HTTPException(422, "a discount is a percent off a product's price: give a product that has a price")

    # One open deposit per account, network, asset, product and discount: reuse it instead of burning an index
    # on every click. A different discount gets its own address.
    existing = db.deposits.find_one({
        "project_id": project["_id"],
        "account_id": body.account_id,
        "chain": chain.key,
        "asset": body.asset,
        "product": body.product,
        "status": Status.PENDING.value,
        "expires_at": {"$gt": now},
        **({"discount.percent": discount["percent"], "discount.ends_at": discount["ends_at"]} if discount else {"discount": None}),
    })
    if existing:
        return Deposit(existing)

    # A fresh address every time the previous one has run out, for this very moment: its path is the
    # time it was made, to the microsecond (api.keys). Expired addresses stay watched, but are never
    # handed out again.
    account_id = int(body.account_id)
    moment = datetime.now(timezone.utc).replace(tzinfo=None)
    deposit = Deposit.new(
        project_id=project["_id"],
        project_number=project["number"],
        account_id=body.account_id,
        product=body.product,
        chain=chain.key,
        family=chain.family.value,
        asset=body.asset,
        # the moment in the path, to the microsecond (MongoDB keeps a time only to the millisecond)
        derived_at=moment.isoformat(timespec="microseconds") + "Z",
        path=full_path(project["number"], chain.family, account_id, moment),
        address=address_at(chain.family, project["xpubs"][chain.family], account_id, moment),
        expected_amount=str(body.expected_amount) if body.expected_amount else None,
        decimals=chain.decimals(body.asset),   # kept, so its amounts read right if the coin leaves the file
        # and its contract, so it is watched and swept there even if the file's contract changes later
        contract=None if chain.is_native(body.asset) else chain.tokens[body.asset].contract,
        created_at=now,
        expires_at=now + timedelta(minutes=setting(project, "deposit_ttl_minutes")),
        # the payment window as opened; expires_at moves (remainder window, reopen), this stays, so a
        # payment can always be judged on time or late against it
        window_ends_at=now + timedelta(minutes=setting(project, "deposit_ttl_minutes")),
        **({"discount": discount} if discount else {}),
    )
    # the same account in the same microsecond (all but impossible): the address is unique in the
    # database, so the clash is refused, and the next microsecond is taken instead
    for _ in range(100):
        try:
            deposit.insert(db)
            break
        except DuplicateKeyError:
            moment += timedelta(microseconds=1)
            deposit = Deposit(deposit.document() | {
                "derived_at": moment.isoformat(timespec="microseconds") + "Z",
                "path": full_path(project["number"], chain.family, account_id, moment),
                "address": address_at(chain.family, project["xpubs"][chain.family], account_id, moment),
            })
    else:
        raise HTTPException(503, "no free address right now: try again")
    notify(db, deposit, "deposit.created", project)
    return deposit
