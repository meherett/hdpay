"""Opening deposits and recording what happens to them, shared by the gateway, the admin and the workers."""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from . import discounts, events
from .chains import CHAINS
from .db import Deposit, Status, find_deposit, utcnow
from .keys import address_at, full_path
from .projects import USD_STABLECOINS, accepted, get_project, price_amount, setting
from .schemas import CreateDeposit, ProjectDepositOut, deposit_decimals


def get_deposit_or_404(db: Database, deposit_id: str, project_id: str | None = None) -> Deposit:
    deposit = find_deposit(db, deposit_id)
    if deposit is None or (project_id is not None and deposit.project_id != project_id):
        raise HTTPException(404, "deposit not found")
    return deposit


def cancel_deposit(db: Database, project: dict[str, Any], deposit_id: str) -> Deposit:
    """The project no longer wants this payment: a pending deposit, with nothing received, becomes
    cancelled. Its address stays watched, like an expired one, so a payment already on its way is still
    counted and reported, never lost; with nothing received it is deleted like an expired one.
    expires_at moves to now: a payment made before the cancel is on time, one after it is late.

    Only a pending deposit: one that received anything, or ended, is not the project's to cancel. Asking
    again for one it already cancelled returns it unchanged, so a retry is safe.
    """
    deposit = get_deposit_or_404(db, deposit_id, project["_id"])
    now = utcnow()
    # in one step, so a payment the watcher records at the same moment is never overwritten
    done = db.deposits.find_one_and_update(
        {"_id": deposit.id, "project_id": project["_id"], "status": Status.PENDING.value, "received_amount": "0"},
        {"$set": {"status": Status.CANCELLED.value, "expires_at": now, "cancelled_at": now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if done is None:
        current = get_deposit_or_404(db, deposit_id, project["_id"])
        if current.status == Status.CANCELLED:
            return current   # cancelled already
        raise HTTPException(409, f"deposit is {current.status.value}: only a pending deposit can be cancelled")
    cancelled = Deposit(done)
    notify(db, cancelled, "deposit.cancelled", project)
    return cancelled


def reopen_for_project(
    db: Database, project: dict[str, Any], deposit_id: str, window: timedelta | None, product: str | None = None
) -> Deposit:
    """The project gives its customer more time for the rest of a short payment: only a deposit in
    review. One the project cancelled is not reopened: the project opens a new deposit, a new address.
    With `product`, the customer changed their mind: the deposit is for that product, at its price,
    what was sent counting toward it. When what was sent already pays that product (within the
    project's amount tolerance), it is paid: confirmed at once, and the project gets deposit.confirmed
    instead of deposit.reopened."""
    deposit = get_deposit_or_404(db, deposit_id, project["_id"])
    if deposit.status == Status.CANCELLED:
        raise HTTPException(409, "deposit is cancelled: open a new deposit instead (POST /v1/deposits)")
    if deposit.status != Status.REVIEW:
        raise HTTPException(409, f"deposit is {deposit.status.value}: only a deposit in review can be reopened")
    if not deposit.expected_amount:
        raise HTTPException(409, "deposit accepts any amount, so there is no rest to wait for")
    if product is not None:
        listed = {p["value"]: p for p in project.get("products") or []}
        if product not in listed or not listed[product].get("price"):
            priced = [v for v, p in listed.items() if p.get("price")]
            raise HTTPException(422, f"product must be one of {', '.join(priced)}" if priced else "there are no priced products")
    apply_reopen(deposit, project, window, product=product)
    if product is not None and paid_in_full(deposit, project):
        # what came pays the product the customer chose: nothing more to send
        deposit.status = Status.CONFIRMED
        deposit.confirmed_at = utcnow()
        deposit.save(db)
        notify_status(db, deposit, project)
        return deposit
    deposit.save(db)
    notify(db, deposit, "deposit.reopened", project)
    return deposit


def paid_in_full(deposit: Deposit, project: dict[str, Any]) -> bool:
    """What the deposit received pays what it expects, within the project's amount tolerance (as the
    watcher judges a payment)."""
    if not deposit.expected_amount:
        return int(deposit.received_amount) > 0
    expected = int(deposit.expected_amount)
    return int(deposit.received_amount) >= expected - expected * setting(project, "amount_tolerance_bps") // 10_000


def reopen(
    db: Database,
    deposit: Deposit,
    project: dict[str, Any] | None,
    window: timedelta | None = None,
    remaining_amount: str | None = None,
    product: str | None = None,
    keep_discount: bool = False,
) -> Deposit:
    """Give a deposit in review, expired or cancelled a fresh window, and tell the project
    (deposit.reopened). The caller checks the status is one it may reopen. See apply_reopen."""
    apply_reopen(deposit, project, window, remaining_amount, product, keep_discount)
    deposit.save(db)
    notify(db, deposit, "deposit.reopened", project)
    return deposit


def apply_reopen(
    deposit: Deposit,
    project: dict[str, Any] | None,
    window: timedelta | None = None,
    remaining_amount: str | None = None,
    product: str | None = None,
    keep_discount: bool = False,
) -> None:
    """The changes of a reopen, not saved yet.

    In review (underpaid, the rest never came): back to partial, waiting for the rest for `window`
    (the project's remainder window by default). Expired or cancelled (nothing came): back to pending,
    a payment in the new `window` (the project's payment window by default) on time, as for a new address.

    `remaining_amount`: the expected amount becomes received + this. `product`: another of the project's
    products; a priced one brings its price, less the deposit's discount while that counts. A discount
    that ended before the deposit was paid is due in full, unless `keep_discount` honors it.
    """
    expired = deposit.status in (Status.EXPIRED, Status.CANCELLED)
    if product is not None:
        listed = {p["value"]: p for p in (project or {}).get("products") or []}
        if product not in listed:
            raise HTTPException(422, f"product must be one of {', '.join(listed)}" if listed else "there are no products")
        price = listed[product].get("price")
        if product != deposit.product and price and deposit.asset in USD_STABLECOINS:
            decimals = deposit_decimals(deposit)
            full = price_amount(price, decimals)
            deposit.expected_amount = str(full)
            if deposit.discount:
                amount = price_amount(discounts.discounted_price(price, deposit.discount["percent"]), decimals)
                deposit.discount = deposit.discount | {"full_amount": str(full), "amount": str(amount)}
                if deposit.discount["status"] != discounts.LAPSED:
                    deposit.expected_amount = str(amount)
        deposit.product = product
    discount = deposit.discount
    if discount and discount["status"] in (discounts.ACTIVE, discounts.LAPSED) and utcnow() >= discount["ends_at"]:
        if keep_discount:
            deposit.discount = discount | {"status": discounts.HONORED}
            deposit.expected_amount = discount["amount"]
        else:
            deposit.discount = discount | {"status": discounts.LAPSED}
            deposit.expected_amount = discount["full_amount"]
    if remaining_amount:
        deposit.expected_amount = str(int(deposit.received_amount) + int(remaining_amount))
    # what it holds already passed its confirmations; a reorg of the rest rolls back to it
    deposit.confirmed_amount = deposit.received_amount
    if expired:
        # a new payment window: a payment in it is on time, as for a new address
        window = window or timedelta(minutes=setting(project or {}, "deposit_ttl_minutes"))
        deposit.expires_at = deposit.window_ends_at = utcnow() + window
        deposit.late = False
        if deposit.status == Status.CANCELLED:
            deposit.cancelled_at = None   # support took it back: it is no longer cancelled
        deposit.status = Status.PENDING
    else:
        window = window or timedelta(minutes=setting(project or {}, "remainder_window_minutes"))
        deposit.expires_at = utcnow() + window
        deposit.status = Status.PARTIAL


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
