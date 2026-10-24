"""
Chain-independent watcher loop. Per loop:

  1. scan new blocks for payments to every watched address (pending, expired, detected, partial)
  2. flip pending -> expired once its window passes (the address keeps being watched)
  3. every ~minute, once caught up, balance-check watched addresses to catch transfers the
     scanner cannot see (internal ETH/BNB transfers made by contracts)
  4. after the chain's confirmations, re-check the transaction (reorg guard) and credit or flag

An underpaid deposit becomes partial and stays watched for its project's remainder window: if the
rest arrives, the new total is checked after its confirmations and, once it is enough, the deposit is
credited and swept. If the window ends first, it goes to review and is no longer watched; the
customer contacts support.

A deposit opened during a product discount (api.discounts) expects the discounted price. Payments
made on the chain before the discount ends count toward it; once the scan has passed the end, a
deposit paid enough in time keeps it, and any other still-watched deposit lapses to the full price
(event deposit.discount_expired): the customer owes the full price less what they sent.

Every status change becomes an event for the deposit's project (api.events), recorded once the
pass is saved.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Any

from pymongo.database import Database

from api import discounts
from api.chains import TxStatus, follow
from api.config import settings
from api.db import Deposit, Status, create_indexes, find_deposits, get_counter, get_db, set_counter, utcnow
from api.deposits import notify, notify_status
from api.projects import projects_by_id, setting
from .base import Scanner

log = logging.getLogger("hdpay.watcher")
WATCHED = (Status.PENDING, Status.EXPIRED, Status.DETECTED, Status.PARTIAL)
MAX_BLOCKS_PER_LOOP = 50
# a payment is looked for from a little before its deposit was opened (clocks and block times drift)
SKIP_MARGIN = timedelta(minutes=10)
BALANCE_CHECK_EVERY_LOOPS = 12

Projects = dict[str, dict[str, Any]]


def amount_ok(deposit: Deposit, project: dict[str, Any]) -> bool:
    received = int(deposit.received_amount)
    if deposit.expected_amount is None:
        return received > 0
    expected = int(deposit.expected_amount)
    floor = expected - expected * setting(project, "amount_tolerance_bps") // 10_000
    return received >= floor


def record_payment(
    deposit: Deposit, tx_hash: str | None, block_number: int, amount: int, paid_at: datetime | None = None
) -> None:
    """`paid_at` is the block's time, needed only for a deposit with a running discount."""
    if paid_at is not None and discounts.in_time(deposit.discount, paid_at):
        deposit.discount = deposit.discount | {"paid_in_time": str(int(deposit.discount["paid_in_time"]) + amount)}
    deposit.received_amount = str(int(deposit.received_amount) + amount)
    deposit.tx_hash = tx_hash
    deposit.block_number = block_number
    if deposit.status == Status.EXPIRED:
        deposit.late = True
    remainder = deposit.status == Status.PARTIAL
    deposit.status = Status.DETECTED
    log.info(
        "payment %s %s to %s on %s (late=%s%s)", amount, deposit.asset, deposit.address, deposit.chain, deposit.late,
        ", the rest of an underpayment" if remainder else "",
    )


def expire(watched: dict[str, Deposit]) -> None:
    now = utcnow()
    for d in watched.values():
        if d.status == Status.PENDING and d.expires_at < now:
            d.status = Status.EXPIRED
        elif d.status == Status.PARTIAL and d.expires_at < now:
            # the rest did not come in time: the address closes and support takes it from here
            d.status = Status.REVIEW
            log.info("deposit %s -> review: the rest of %s did not arrive in time", d.id, d.received_amount)


def lapse(watched: dict[str, Deposit], scanned_to: datetime, projects: Projects) -> list[Deposit]:
    """Settle the discounts that ended before the last scanned block: every payment made in time is
    counted by now. Returns the deposits that lapsed to the full price.

    Only a deposit that received something: one with nothing keeps its discounted amount after the end
    (it shows as ended), and lapses when a payment does arrive, since that payment came too late."""
    lapsed = []
    for d in watched.values():
        if not settles(d, scanned_to):
            continue
        discount = d.discount
        status, full = discounts.settle(discount, setting(projects.get(d.project_id, {}), "amount_tolerance_bps"))
        d.discount = discount | {"status": status}
        if full is not None:
            d.expected_amount = full
            lapsed.append(d)
            log.info("deposit %s: the %s%% discount ended unpaid, %s is now due", d.id, discount["percent"], full)
    return lapsed


def settles(d: Deposit, at: datetime) -> bool:
    """A running discount that ended by `at` on a deposit that received something."""
    discount = d.discount
    return bool(
        discount and discount["status"] == discounts.ACTIVE and at >= discount["ends_at"] and int(d.received_amount) > 0
    )


def balance_fallback(scanner: Scanner, block: int, watched: dict[str, Deposit]) -> None:
    """Count what the address holds beyond what was recorded, for payments the scan cannot see.

    Native coins: internal transfers made by contracts. Every asset: a payment in a block that was
    scanned while nobody was waiting for it. That is one sent before its deposit was opened, one
    from before a watcher's first run (it starts from the newest block), one from while it was
    stopped beyond what it catches up, and one sent to a reopened deposit while it was in review.
    Nothing is swept before a deposit is confirmed, so the balance is everything it received.
    """
    for address, d in watched.items():
        if d.status not in (Status.PENDING, Status.EXPIRED, Status.PARTIAL):
            continue
        balance = scanner.balance_at(address, d.asset, block)
        seen = int(d.received_amount)
        if balance is not None and balance > seen:
            # when it was sent is unknown: the block it was found in, which never favours a discount
            paid_at = (scanner.block_time(block) or utcnow()) if d.discount else None
            record_payment(d, None, block, balance - seen, paid_at)


def confirm(scanner: Scanner, latest: int, watched: dict[str, Deposit], projects: Projects) -> None:
    for d in watched.values():
        if d.status != Status.DETECTED or d.block_number is None:
            continue
        if latest - d.block_number + 1 < scanner.chain.confirmations:
            continue
        if d.tx_hash:
            status = scanner.tx_status(d.tx_hash, d.block_number)
            if status == TxStatus.UNKNOWN:
                continue
            if status == TxStatus.FAILED:
                # reorged out or failed: back to what was already confirmed; an earlier payment stays counted
                kept = d.confirmed_amount or "0"
                log.warning("tx %s vanished/failed, %s back to %s received", d.tx_hash, d.address, kept)
                d.received_amount, d.tx_hash, d.block_number = kept, None, None
                if d.discount and int(d.discount["paid_in_time"]) > int(kept):
                    d.discount = d.discount | {"paid_in_time": kept}
                if int(kept) > 0:
                    d.status = Status.PARTIAL   # keeps its window; expire() closes it if that has passed
                else:
                    d.status = Status.EXPIRED if d.expires_at < utcnow() else Status.PENDING
                continue
        project = projects.get(d.project_id, {})
        already_partial = int(d.confirmed_amount or "0") > 0
        d.confirmed_amount = d.received_amount
        if amount_ok(d, project):
            d.status = Status.CONFIRMED   # late-but-correct payments, and completed remainders, are credited
            d.confirmed_at = utcnow()
            if d.discount and d.discount["status"] == discounts.ACTIVE:
                d.discount = d.discount | {"status": discounts.KEPT}
        else:
            # underpaid: the same address waits for the rest; the window starts at the first short payment
            if not already_partial:
                d.expires_at = utcnow() + timedelta(minutes=setting(project, "remainder_window_minutes"))
            d.status = Status.PARTIAL
        log.info("deposit %s -> %s (late=%s)", d.id, d.status.value, d.late)


def skip_ahead(scanner: Scanner, cursor: int, latest: int, deposits: list[Deposit]) -> int:
    """Far behind (the watcher was stopped, or its database has an old cursor): blocks from before the
    oldest watched deposit was opened cannot pay any of them, so they are skipped instead of read one
    by one. The first block to read is found by its time; with no deposits to watch, the head."""
    if latest - cursor <= MAX_BLOCKS_PER_LOOP:
        return cursor
    if not deposits:
        log.info("nothing to watch on %s: skipping to the head, block %d", scanner.chain.key, latest)
        return latest - 1
    oldest = min(d.created_at for d in deposits) - SKIP_MARGIN
    first = cursor + 1
    first_time = scanner.block_time(first)
    if first_time is None or first_time >= oldest:
        return cursor
    lo, hi = first, latest   # lo is before `oldest`; the first block at or after it is in (lo, hi]
    while hi - lo > 1:
        mid = (lo + hi) // 2
        mid_time = scanner.block_time(mid)
        if mid_time is None:
            return cursor   # the chain cannot say: read every block rather than risk a miss
        if mid_time < oldest:
            lo = mid
        else:
            hi = mid
    log.info(
        "skipping %s blocks %d to %d: before the oldest watched deposit was opened (%s)",
        scanner.chain.key, first, lo, oldest + SKIP_MARGIN,
    )
    return lo


def run_once(db: Database, scanner: Scanner, loops: int) -> bool:
    """One pass; True once the watcher has caught up with the chain."""
    chain = scanner.chain
    cursor_key = f"last_block:{chain.key}"
    cursor = get_counter(db, cursor_key) or 0
    latest = scanner.latest_block()
    if cursor == 0:
        cursor = latest - 1   # first run: start from now

    projects = projects_by_id(db)
    found = find_deposits(db, {"chain": chain.key, "status": {"$in": [s.value for s in WATCHED]}})
    watched = {scanner.normalize(d.address): d for d in found}
    before = {d.id: d.status for d in found}
    cursor = skip_ahead(scanner, cursor, latest, found)

    end = min(latest, cursor + MAX_BLOCKS_PER_LOOP)
    times: dict[int, datetime] = {}

    def block_time(block: int) -> datetime:
        """A block's time, read once per pass; the clock when the chain cannot say."""
        if block not in times:
            times[block] = scanner.block_time(block) or utcnow()
        return times[block]

    if watched and end > cursor:
        assets = {d.asset for d in watched.values()}
        for p in scanner.scan(cursor + 1, end, set(watched), assets):
            d = watched[p.address]
            if p.asset != d.asset:
                log.warning(
                    "%s %s sent to %s deposit %s in %s — not credited, recover manually",
                    p.amount, p.asset, d.asset, d.id, p.tx_hash,
                )
                continue
            paid_at = block_time(p.block_number) if d.discount else None
            record_payment(d, p.tx_hash, p.block_number, p.amount, paid_at)

    expire(watched)
    # only when caught up: a balance read ahead of the cursor would count a payment the scan sees again later
    if loops % BALANCE_CHECK_EVERY_LOOPS == 0 and end == latest:
        balance_fallback(scanner, latest, watched)
    lapsed = []
    now = utcnow()
    if any(settles(d, now) for d in watched.values()):
        # only up to what has been scanned: a payment made in time but not scanned yet still counts
        lapsed = lapse(watched, block_time(max(cursor, end)), projects)
    confirm(scanner, latest, watched, projects)

    # save the deposits before the cursor moves on, so a crash re-scans rather than loses a payment
    for d in watched.values():
        d.save(db)
    set_counter(db, cursor_key, max(cursor, end))
    # read by the admin overview: how far behind the chain head, and when this watcher last ran
    set_counter(db, f"head:{chain.key}", latest)
    set_counter(db, f"heartbeat:{chain.key}", int(time.time()))

    for d in lapsed:
        notify(db, d, "deposit.discount_expired", projects.get(d.project_id))
    for d in watched.values():
        if d.status != before[d.id]:
            notify_status(db, d, projects.get(d.project_id))
    return end >= latest


def run(scanner: Scanner, build=None) -> None:
    """`build` makes a scanner for a chain, so an edit to the chains file is applied while running."""
    log.info("watching %s (%d confirmations)", scanner.chain.name, scanner.chain.confirmations)
    db = get_db()
    create_indexes(db)
    loops = 0
    while True:
        try:
            if build:
                scanner = follow(scanner, build)
            caught_up = run_once(db, scanner, loops)
        except Exception:
            log.exception("watcher loop failed")
            caught_up = True   # wait before trying again
        loops += 1
        if caught_up:
            time.sleep(settings.poll_interval_seconds)   # behind: straight on to the next blocks
