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

Deadlines are judged on the chain's clock, not the watcher's: a payment counts as on time when its
block is before the deadline, and a deposit expires (or goes to review) only once the scan has read
past its deadline. A watcher that was stopped and catches up later therefore judges each payment as
it would have live. Every payment is listed on its deposit by transaction, so none is counted twice,
whether the scan, a balance check or a support check finds it.

Support can ask for a check of a deposit in the admin (deposit.check): the watcher rescans that address
from just before the deposit was opened, counts any payment it finds with the time of its block, and
compares the balance, so a deposit in review whose rest was paid in time is credited after all.

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
from api.balances import rpc_problem
from api.chains import TxStatus, deposit_token, follow, handles
from api.config import settings
from api.db import Deposit, Status, create_indexes, find_deposits, get_counter, get_db, set_counter, utcnow
from api.deposits import notify, notify_status
from api.projects import projects_by_id, setting
from .base import Scanner

log = logging.getLogger("hdpay.watcher")
WATCHED = (Status.PENDING, Status.EXPIRED, Status.CANCELLED, Status.DETECTED, Status.PARTIAL)
# closed with nothing received, still watched: a payment to one is counted late
CLOSED = (Status.EXPIRED, Status.CANCELLED)
MAX_BLOCKS_PER_LOOP = 50
# a support check rescans its address in pieces this long
CHECK_CHUNK_BLOCKS = 500
# when the chain cannot tell the time of a block, a check reads this many blocks back
CHECK_FALLBACK_BLOCKS = 5_000
# a check reads for at most this long per pass, then the watcher carries on watching
CHECK_SECONDS_PER_PASS = 20
# a payment is looked for from a little before its deposit was opened (clocks and block times drift)
SKIP_MARGIN = timedelta(minutes=10)
BALANCE_CHECK_EVERY_LOOPS = 12
DELETE_PER_PASS = 50   # expired deposits deleted per pass at most: each costs one balance read

Projects = dict[str, dict[str, Any]]


def amount_ok(deposit: Deposit, project: dict[str, Any]) -> bool:
    received = int(deposit.received_amount)
    if deposit.expected_amount is None:
        return received > 0
    expected = int(deposit.expected_amount)
    floor = expected - expected * setting(project, "amount_tolerance_bps") // 10_000
    return received >= floor


def counted(d: Deposit, tx_hash: str | None, block_number: int) -> bool:
    """Already in the deposit's received amount: listed by its transaction, or in a block a balance
    read covered (see Deposit.counted_to)."""
    if tx_hash and any(p.get("tx_hash") == tx_hash for p in d.payments):
        return True
    upto = d.counted_to
    return upto is not None and block_number <= upto


def list_payment(d: Deposit, entry: dict[str, Any]) -> None:
    """Add a payment to the deposit's list. A deposit from before payments were listed keeps its last
    counted block as a mark, so the transfers it counted then are never counted again."""
    if not d.has("payments") and int(d.received_amount) > 0 and d.block_number is not None:
        d.legacy_counted_to = d.block_number
    d.payments = d.payments + [entry]


def as_of(d: Deposit, at: datetime) -> None:
    """The deposit's status as it was at `at` on the chain. A deposit marked expired or in review by
    the clock, while its payment was made before the deadline, is judged by the payment's time."""
    if d.status in CLOSED and at <= d.expires_at:
        d.status = Status.PENDING   # paid before it expired, or before it was cancelled: on time
    elif d.status == Status.REVIEW and at <= d.expires_at:
        d.status = Status.PARTIAL
    expire_one(d, at)


def expire_one(d: Deposit, at: datetime) -> None:
    if d.status == Status.PENDING and d.expires_at < at:
        d.status = Status.EXPIRED
    elif d.status == Status.PARTIAL and d.expires_at < at:
        # the rest did not come in time: the address closes and support takes it from here
        d.status = Status.REVIEW
        log.info("deposit %s -> review: the rest of %s did not arrive in time", d.id, d.received_amount)


def record_payment(
    deposit: Deposit, tx_hash: str | None, block_number: int, amount: int, paid_at: datetime, source: str = "scan"
) -> bool:
    """Count a payment made at `paid_at` (its block's time). False when it was already counted, or the
    deposit is paid. One that comes after the remainder window is counted but leaves the deposit in
    review: support decides."""
    d = deposit
    if d.status in (Status.CONFIRMED, Status.SWEPT) or counted(d, tx_hash, block_number):
        return False
    as_of(d, paid_at)
    if d.status == Status.PENDING and int(d.received_amount) == 0:
        d.late = False   # the first payment, inside the window: on time, whatever the clock had said
    if d.discount and discounts.in_time(d.discount, paid_at):
        d.discount = d.discount | {"paid_in_time": str(int(d.discount["paid_in_time"]) + amount)}
    list_payment(d, {
        "tx_hash": tx_hash, "block_number": block_number, "amount": str(amount), "paid_at": paid_at,
        "source": source, "confirmed": False,
    })
    d.received_amount = str(int(d.received_amount) + amount)
    d.tx_hash = tx_hash
    d.block_number = block_number
    if d.status == Status.REVIEW:
        log.info("payment %s %s to %s after its remainder window: counted, still for support", amount, d.asset, d.address)
        return True
    if d.status in CLOSED:
        d.late = True
    remainder = d.status == Status.PARTIAL
    d.status = Status.DETECTED
    log.info(
        "payment %s %s to %s on %s (late=%s%s)", amount, d.asset, d.address, d.chain, d.late,
        ", the rest of an underpayment" if remainder else "",
    )
    return True


def expire(watched: dict[str, Deposit], scanned_to: datetime) -> None:
    """Deadlines that the scan has read past: a payment made before them would have been seen by now."""
    for d in watched.values():
        expire_one(d, scanned_to)


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


def balance_fallback(scanner: Scanner, block: int, watched: dict[str, Deposit], block_time) -> None:
    """Count what the address holds beyond what was recorded, for payments the scan cannot see.

    Native coins: internal transfers made by contracts. Every asset: a payment in a block that was
    scanned while nobody was waiting for it. That is one sent before its deposit was opened, one
    from before a watcher's first run (it starts from the newest block), one from while it was
    stopped beyond what it catches up, and one sent to a reopened deposit while it was in review.
    Nothing is swept before a deposit is confirmed, so the balance is everything it received.
    """
    for address, d in watched.items():
        if d.status in (Status.PENDING, *CLOSED, Status.PARTIAL):
            count_balance(scanner, address, d, block, block_time)


def count_balance(scanner: Scanner, address: str, d: Deposit, block: int, block_time) -> int:
    """Count what the address holds beyond what was recorded; returns the amount counted. When it was
    sent is unknown: the block the balance covers, which never favours a deadline or a discount."""
    if not handles(scanner.chain, d):
        return 0   # a token whose contract is not known: nothing to ask
    token = deposit_token(scanner.chain, d)
    read = scanner.balance_covering(address, d.asset, block, token.contract if token else None)
    if read is None:
        return 0
    balance, covered = read
    extra = balance - int(d.received_amount)
    if extra > 0 and record_payment(d, None, covered, extra, block_time(covered), "balance"):
        d.balance_block = max(covered, d.counted_to or 0)   # the scan skips what this balance includes
        return extra
    return 0


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
                d.payments = [p for p in d.payments if p.get("confirmed")]
                if d.discount and int(d.discount["paid_in_time"]) > int(kept):
                    d.discount = d.discount | {"paid_in_time": kept}
                if int(kept) > 0:
                    d.status = Status.PARTIAL   # keeps its window; expire() closes it if that has passed
                else:
                    if d.has("cancelled_at") and d.cancelled_at:
                        d.status = Status.CANCELLED
                    else:
                        d.status = Status.EXPIRED if d.expires_at < utcnow() else Status.PENDING
                continue
        project = projects.get(d.project_id, {})
        already_partial = int(d.confirmed_amount or "0") > 0
        d.confirmed_amount = d.received_amount
        d.payments = [p | {"confirmed": True} for p in d.payments]
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
    first = first_block_at(scanner, oldest, cursor + 1, latest)
    if first is None or first <= cursor + 1:
        return cursor   # nothing before it to skip, or the chain cannot say: read every block
    log.info(
        "skipping %s blocks %d to %d: before the oldest watched deposit was opened (%s)",
        scanner.chain.key, cursor + 1, first - 1, oldest + SKIP_MARGIN,
    )
    return first - 1


def first_block_at(scanner: Scanner, when: datetime, lo: int, hi: int) -> int | None:
    """The first block in lo..hi made at or after `when`, by halving the range; None when the chain
    cannot tell a block's time."""
    lo_time = scanner.block_time(lo)
    if lo_time is None:
        return None
    if lo_time >= when:
        return lo
    while hi - lo > 1:   # lo is before `when`; the answer is in (lo, hi]
        mid = (lo + hi) // 2
        mid_time = scanner.block_time(mid)
        if mid_time is None:
            return None
        if mid_time < when:
            lo = mid
        else:
            hi = mid
    return hi


def run_checks(
    scanner: Scanner, scanned_to: int, watched: dict[str, Deposit], requested: list[Deposit], block_time
) -> list[Deposit]:
    """Run the support checks asked for on this chain; returns the deposits checked."""
    by_id = {d.id: d for d in watched.values()}
    done = []
    for fresh in requested:
        d = by_id.get(fresh.id, fresh)   # the same object the pass already works on, if it is watched
        try:
            check_deposit(scanner, d, scanned_to, block_time)
        except Exception as exc:   # one failed check must not stop the watcher; it is tried again next pass
            log.exception("checking deposit %s failed", d.id)
            d.check = (d.check or {}) | {"error": rpc_problem(exc)}
        done.append(d)
    return done


def check_deposit(scanner: Scanner, d: Deposit, scanned_to: int, block_time) -> None:
    """Rescan the deposit's address from just before it was opened up to the scan, a slice of at most
    CHECK_SECONDS_PER_PASS at a time (the check keeps its place, so the watcher is never held up for
    long). Every payment not yet counted is counted with the time of its block; once the range is read,
    the balance is compared for what no transfer shows (coins sent through a contract)."""
    address = scanner.normalize(d.address)
    check = dict(d.check or {})
    if not handles(scanner.chain, d):
        d.check = check | {
            "status": "done", "checked_at": utcnow(), "found": [], "counted": "0", "from_balance": "0",
            "error": f"{d.asset} on {scanner.chain.name}: its contract is not known, so it cannot be checked",
        }
        return
    token = deposit_token(scanner.chain, d)
    if check.get("next_block") is None:   # a new check: where its range starts
        start = first_block_at(scanner, d.created_at - SKIP_MARGIN, 1, scanned_to)
        check |= {"exact": start is not None, "found": [], "counted": "0", "from_balance": "0", "checked_at": None}
        if start is None:
            start = max(scanned_to - CHECK_FALLBACK_BLOCKS, 1)
        check |= {"from_block": start, "next_block": start}
    began = time.monotonic()
    a = check["next_block"]
    counted_now = int(check.get("counted") or 0)
    found = list(check.get("found") or [])
    while a <= scanned_to:   # at least one slice per pass, so a check always moves on
        b = min(a + CHECK_CHUNK_BLOCKS - 1, scanned_to)
        for p in sorted(scanner.scan(a, b, {address}, {d.asset}, [token] if token else []),
                        key=lambda p: p.block_number):
            if p.asset != d.asset:
                continue
            paid_at = block_time(p.block_number)
            found.append({"tx_hash": p.tx_hash, "amount": str(p.amount), "paid_at": paid_at})
            if record_payment(d, p.tx_hash, p.block_number, p.amount, paid_at, "check"):
                counted_now += p.amount
            elif not any(e.get("tx_hash") == p.tx_hash for e in d.payments):
                # counted long ago, before payments were listed: listed now, for the record
                list_payment(d, {
                    "tx_hash": p.tx_hash, "block_number": p.block_number, "amount": str(p.amount),
                    "paid_at": paid_at, "source": "scan", "confirmed": True,
                })
        a = b + 1
        if time.monotonic() - began >= CHECK_SECONDS_PER_PASS:
            break
    check |= {"next_block": a, "to_block": scanned_to, "found": found, "counted": str(counted_now), "error": None}
    if a <= scanned_to:
        d.check = check   # more to read: the next pass carries on from here
        log.info("checking deposit %s: %s blocks read up to %d of %d", d.id, scanner.chain.key, a - 1, scanned_to)
        return
    from_balance = 0
    if d.status not in (Status.CONFIRMED, Status.SWEPT):
        from_balance = count_balance(scanner, address, d, scanned_to, block_time)
    # late is judged again by the first payment's block against the payment window as it was opened
    window = d.window_ends_at if d.has("window_ends_at") else d.expires_at
    times = [e["paid_at"] for e in d.payments if e.get("paid_at") is not None and e.get("source") != "balance"]
    if d.late and times and min(times) <= window:
        d.late = False
    d.check = check | {
        "status": "done", "checked_at": utcnow(), "next_block": None,
        "counted": str(counted_now + from_balance), "from_balance": str(from_balance),
    }
    log.info(
        "checked deposit %s (%s blocks %d to %d): %d payment(s) on the chain, %s newly counted",
        d.id, scanner.chain.key, check["from_block"], scanned_to, len(found), counted_now + from_balance,
    )

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
        # each deposit's own contract: one opened before the file's contract changed keeps its old one
        tokens = {t.contract.lower(): t for d in watched.values() if (t := deposit_token(chain, d))}
        for p in sorted(scanner.scan(cursor + 1, end, set(watched), assets, list(tokens.values())),
                        key=lambda p: p.block_number):
            d = watched[p.address]
            if p.asset != d.asset:
                log.warning(
                    "%s %s sent to %s deposit %s in %s — not credited, recover manually",
                    p.amount, p.asset, d.asset, d.id, p.tx_hash,
                )
                continue
            record_payment(d, p.tx_hash, p.block_number, p.amount, block_time(p.block_number))

    scanned_to = max(cursor, end)
    expire(watched, block_time(scanned_to))
    # only when caught up: a balance read ahead of the cursor would count a payment the scan sees again later
    if loops % BALANCE_CHECK_EVERY_LOOPS == 0 and end == latest:
        balance_fallback(scanner, latest, watched, block_time)
    # support checks asked for in the admin; a deposit not watched (in review) is checked all the same
    requested = find_deposits(db, {"chain": chain.key, "check.status": "waiting"})
    watched_ids = {d.id for d in watched.values()}
    for r in requested:
        before.setdefault(r.id, r.status)   # a deposit in review is not watched: its status before the check
    checked = run_checks(scanner, scanned_to, watched, requested, block_time) if requested else []
    others = [d for d in checked if d.id not in watched_ids]
    lapsed = []
    now = utcnow()
    if any(settles(d, now) for d in watched.values()):
        # only up to what has been scanned: a payment made in time but not scanned yet still counts
        lapsed = lapse(watched, block_time(max(cursor, end)), projects)
    confirm(scanner, latest, watched, projects)

    # save the deposits before the cursor moves on, so a crash re-scans rather than loses a payment
    for d in [*watched.values(), *others]:
        d.save(db)
    set_counter(db, cursor_key, scanned_to)
    # read by the admin overview: how far behind the chain head, and when this watcher last ran
    set_counter(db, f"head:{chain.key}", latest)
    set_counter(db, f"heartbeat:{chain.key}", int(time.time()))

    for d in lapsed:
        notify(db, d, "deposit.discount_expired", projects.get(d.project_id))
    for d in [*watched.values(), *others]:
        if d.status != before[d.id]:
            notify_status(db, d, projects.get(d.project_id))
    if end == latest:
        delete_expired(db, scanner, latest, watched, block_time, projects)
    return end >= latest


def deletable(d: Deposit, now: datetime) -> bool:
    """Expired or cancelled with nothing received, long enough ago (HDPAY_DELETE_UNPAID_AFTER_HOURS; 0: never)."""
    hours = settings.delete_unpaid_after_hours
    return (
        hours > 0 and d.status in CLOSED and int(d.received_amount) == 0 and not d.payments
        and not (d.check and d.check.get("status") == "waiting")
        and d.expires_at <= now - timedelta(hours=hours)
    )


def delete_expired(db: Database, scanner: Scanner, latest: int, watched: dict[str, Deposit], block_time,
                   projects: Projects) -> list[Deposit]:
    """Delete the expired deposits that never received anything, after one last look at their balance:
    a deposit that holds something after all is counted (detected) and kept. Only once caught up, so
    every block up to the head was scanned first. Returns the deleted ones."""
    now, deleted = utcnow(), []
    for address, d in watched.items():
        if len(deleted) >= DELETE_PER_PASS:
            break
        if not deletable(d, now):
            continue
        try:
            read = scanner.balance_covering(address, d.asset, latest, (t := deposit_token(scanner.chain, d)) and t.contract)
        except Exception as exc:
            log.warning("deposit %s: its balance could not be read (%s); deleted on a later pass", d.id, rpc_problem(exc))
            continue
        if read is None:
            continue   # a balance this chain cannot read: kept, never deleted unseen
        if read[0] > 0:
            if count_balance(scanner, address, d, latest, block_time):
                d.save(db)
                notify_status(db, d, projects.get(d.project_id))
            continue
        # only if nothing changed it meanwhile (a support check, a payment)
        gone = db.deposits.delete_one(
            {"_id": d.id, "status": {"$in": [s.value for s in CLOSED]}, "received_amount": "0"}
        )
        if gone.deleted_count:
            notify(db, d, "deposit.deleted", projects.get(d.project_id))   # from the copy in hand
            deleted.append(d)
            log.info("deposit %s (%s) expired %s with nothing received: deleted", d.id, d.address, d.expires_at)
    return deleted


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
