from datetime import timedelta

import pytest

from factories import make_project
from api.chains import TxStatus
from api.db import Deposit, Status, find_deposit, get_counter, get_db, set_counter, utcnow
from watcher import core
from stubs import StubScanner

CONFIRMATIONS = 20   # tron-nile


@pytest.fixture(autouse=True)
def project(mongo):
    return make_project(mongo)


def make_deposit(asset="USDT", expected=10_000_000, address="TDeposit0", minutes=30, index=0):
    d = Deposit.new(
        project_id=make_deposit.project_id(), project_number=48213, account_id="1", product=None,
        chain="tron-nile", family="tron", asset=asset, address=address,
        derivation_date=utcnow().date().isoformat(), index=index, path=f"m/48213'/195'/1/2026/10/21/{index}",
        expected_amount=str(expected) if expected else None,
        expires_at=utcnow() + timedelta(minutes=minutes),
    )
    d.insert(get_db())
    return d.id


make_deposit.project_id = lambda: get_db().projects.find_one()["_id"]


def update(deposit_id, **fields):
    get_db().deposits.update_one({"_id": deposit_id}, {"$set": {k: v.value if isinstance(v, Status) else v for k, v in fields.items()}})


def tick(scanner, loops=1):
    # loops=1 keeps the balance fallback (every 12th loop) off unless a test asks for it
    core.run_once(get_db(), scanner, loops)


def get(deposit_id):
    return find_deposit(get_db(), deposit_id)


def event_types():
    return [e["type"] for e in get_db().events.find().sort("seq", 1)]


@pytest.fixture
def chain():
    s = StubScanner()
    tick(s)          # first run anchors the cursor at head - 1
    return s


def advance(scanner, blocks):
    scanner.head += blocks
    tick(scanner)


def test_on_time_payment_confirms_after_confirmations(chain):
    d = make_deposit()
    chain.pay("TDeposit0", "USDT", 10_000_000, "tx1", block=chain.head + 1)
    advance(chain, 1)
    assert get(d).status == Status.DETECTED
    advance(chain, CONFIRMATIONS - 2)
    assert get(d).status == Status.DETECTED
    advance(chain, 1)
    got = get(d)
    assert (got.status, got.late, got.received_amount, got.tx_hash) == (Status.CONFIRMED, False, "10000000", "tx1")


def test_expired_address_is_still_watched_and_late_payment_credited(chain):
    d = make_deposit(minutes=-1)
    tick(chain)
    assert get(d).status == Status.EXPIRED
    chain.pay("TDeposit0", "USDT", 10_000_000, "tx1", block=chain.head + 1)
    advance(chain, CONFIRMATIONS)
    got = get(d)
    assert (got.status, got.late) == (Status.CONFIRMED, True)


def test_underpayment_within_tolerance_is_credited(chain):
    d = make_deposit(expected=10_000_000)
    chain.pay("TDeposit0", "USDT", 9_950_000, "tx1", block=chain.head + 1)   # exactly 0.50% short
    advance(chain, CONFIRMATIONS)
    assert get(d).status == Status.CONFIRMED


def close_window(deposit_id):
    """Move the deposit's deadline into the past, as if the remainder window ran out."""
    update(deposit_id, expires_at=utcnow() - timedelta(seconds=1))


def test_underpayment_waits_for_the_rest_on_the_same_address(chain, monkeypatch):
    monkeypatch.setattr(core.settings, "remainder_window_minutes", 1440)
    d = make_deposit(expected=10_000_000)
    chain.pay("TDeposit0", "USDT", 5_000_000, "tx1", block=chain.head + 1)
    advance(chain, CONFIRMATIONS)
    got = get(d)
    assert (got.status, got.confirmed_amount) == (Status.PARTIAL, "5000000")
    # the window restarts from the short payment: one day here
    left = (got.expires_at - utcnow()).total_seconds()
    assert 1439 * 60 < left <= 1440 * 60


def test_the_rest_in_time_completes_the_deposit(chain):
    d = make_deposit(expected=10_000_000)
    chain.pay("TDeposit0", "USDT", 4_000_000, "tx1", block=chain.head + 1)
    advance(chain, CONFIRMATIONS)
    chain.pay("TDeposit0", "USDT", 6_000_000, "tx2", block=chain.head + 1)
    advance(chain, 1)
    assert (get(d).status, get(d).received_amount) == (Status.DETECTED, "10000000")
    advance(chain, CONFIRMATIONS)
    got = get(d)
    assert (got.status, got.received_amount, got.tx_hash) == (Status.CONFIRMED, "10000000", "tx2")
    assert got.confirmed_at is not None


def test_a_remainder_still_short_keeps_the_first_deadline(chain):
    d = make_deposit(expected=10_000_000)
    chain.pay("TDeposit0", "USDT", 4_000_000, "tx1", block=chain.head + 1)
    advance(chain, CONFIRMATIONS)
    deadline = get(d).expires_at
    chain.pay("TDeposit0", "USDT", 1_000_000, "tx2", block=chain.head + 1)
    advance(chain, CONFIRMATIONS + 1)
    got = get(d)
    assert (got.status, got.received_amount, got.expires_at) == (Status.PARTIAL, "5000000", deadline)


def test_when_the_window_ends_the_address_closes_for_support(chain):
    d = make_deposit(expected=10_000_000)
    chain.pay("TDeposit0", "USDT", 4_000_000, "tx1", block=chain.head + 1)
    advance(chain, CONFIRMATIONS)
    close_window(d)
    tick(chain)
    assert get(d).status == Status.REVIEW
    # no longer watched: a payment after the window is not counted
    chain.pay("TDeposit0", "USDT", 6_000_000, "tx2", block=chain.head + 1)
    advance(chain, CONFIRMATIONS + 1)
    assert (get(d).status, get(d).received_amount) == (Status.REVIEW, "4000000")


def test_a_reorged_remainder_keeps_the_confirmed_first_payment(chain):
    d = make_deposit(expected=10_000_000)
    chain.pay("TDeposit0", "USDT", 4_000_000, "tx1", block=chain.head + 1)
    advance(chain, CONFIRMATIONS)
    chain.pay("TDeposit0", "USDT", 6_000_000, "tx2", block=chain.head + 1)
    advance(chain, 1)
    chain.statuses["tx2"] = TxStatus.FAILED
    advance(chain, CONFIRMATIONS)
    got = get(d)
    assert (got.status, got.received_amount) == (Status.PARTIAL, "4000000")


def test_balance_fallback_sees_the_rest_of_a_native_payment(chain):
    d = make_deposit(asset="TRX", expected=10_000_000)
    chain.pay("TDeposit0", "TRX", 4_000_000, "tx1", block=chain.head + 1)
    advance(chain, CONFIRMATIONS)
    assert get(d).status == Status.PARTIAL
    chain.balances[("TDeposit0", "TRX")] = 10_000_000      # the rest came by an internal transfer
    tick(chain, loops=core.BALANCE_CHECK_EVERY_LOOPS)
    assert (get(d).status, get(d).received_amount) == (Status.DETECTED, "10000000")


def test_a_reopened_token_deposit_counts_what_was_sent_while_it_was_closed(chain):
    d = make_deposit(expected=10_000_000)
    chain.pay("TDeposit0", "USDT", 4_000_000, "tx1", block=chain.head + 1)
    advance(chain, CONFIRMATIONS)
    close_window(d)
    tick(chain)
    assert get(d).status == Status.REVIEW
    # the rest is sent while the address is closed: the scan never sees it
    chain.pay("TDeposit0", "USDT", 6_000_000, "tx2", block=chain.head + 1)
    advance(chain, 2)
    chain.balances[("TDeposit0", "USDT")] = 10_000_000
    # support reopens it
    update(d, status=Status.PARTIAL, expires_at=utcnow() + timedelta(minutes=30))
    tick(chain, loops=core.BALANCE_CHECK_EVERY_LOOPS)
    assert (get(d).status, get(d).received_amount) == (Status.DETECTED, "10000000")
    advance(chain, CONFIRMATIONS)
    assert get(d).status == Status.CONFIRMED


def test_a_token_payment_the_scan_never_saw_is_found_in_the_balance(chain):
    # sent before the deposit was opened, or before the watcher's first run: no scan will see it
    d = make_deposit(expected=10_000_000)
    chain.balances[("TDeposit0", "USDT")] = 10_000_000
    tick(chain, loops=core.BALANCE_CHECK_EVERY_LOOPS)
    assert (get(d).status, get(d).received_amount) == (Status.DETECTED, "10000000")
    advance(chain, CONFIRMATIONS)
    assert (get(d).status, get(d).received_amount) == (Status.CONFIRMED, "10000000")


def test_a_token_payment_the_scan_saw_is_not_counted_again_from_the_balance(chain):
    d = make_deposit(expected=10_000_000)
    chain.pay("TDeposit0", "USDT", 10_000_000, "tx1", block=chain.head + 1)
    chain.balances[("TDeposit0", "USDT")] = 10_000_000
    advance(chain, CONFIRMATIONS)
    tick(chain, loops=core.BALANCE_CHECK_EVERY_LOOPS)
    assert (get(d).status, get(d).received_amount) == (Status.CONFIRMED, "10000000")


def test_any_amount_deposit(chain):
    d = make_deposit(expected=None)
    chain.pay("TDeposit0", "USDT", 1, "tx1", block=chain.head + 1)
    advance(chain, CONFIRMATIONS)
    assert get(d).status == Status.CONFIRMED


def test_two_payments_add_up(chain):
    d = make_deposit(expected=10_000_000)
    chain.pay("TDeposit0", "USDT", 4_000_000, "tx1", block=chain.head + 1)
    chain.pay("TDeposit0", "USDT", 6_000_000, "tx2", block=chain.head + 2)
    advance(chain, CONFIRMATIONS + 1)
    got = get(d)
    assert (got.status, got.received_amount) == (Status.CONFIRMED, "10000000")


def test_reorged_payment_is_rolled_back_and_watched_again(chain):
    d = make_deposit()
    chain.pay("TDeposit0", "USDT", 10_000_000, "tx1", block=chain.head + 1)
    advance(chain, 1)
    chain.statuses["tx1"] = TxStatus.FAILED
    advance(chain, CONFIRMATIONS)
    got = get(d)
    assert (got.status, got.received_amount, got.tx_hash) == (Status.PENDING, "0", None)


def test_unknown_status_waits_instead_of_resetting(chain):
    d = make_deposit()
    chain.pay("TDeposit0", "USDT", 10_000_000, "tx1", block=chain.head + 1)
    chain.statuses["tx1"] = TxStatus.UNKNOWN
    advance(chain, CONFIRMATIONS)
    assert get(d).status == Status.DETECTED
    chain.statuses["tx1"] = TxStatus.OK
    advance(chain, 1)
    assert get(d).status == Status.CONFIRMED


def test_wrong_asset_is_not_credited(chain):
    usdt = make_deposit(asset="USDT", address="TDeposit0", index=0)
    make_deposit(asset="TRX", address="TDeposit1", index=1)   # so TRX is scanned at all
    chain.pay("TDeposit0", "TRX", 50_000_000, "tx1", block=chain.head + 1)
    advance(chain, CONFIRMATIONS)
    got = get(usdt)
    assert (got.status, got.received_amount) == (Status.PENDING, "0")


def test_balance_fallback_catches_unseen_transfer(chain):
    d = make_deposit(asset="TRX", expected=None)
    chain.balances[("TDeposit0", "TRX")] = 7
    chain.head += 1
    tick(chain, loops=0)
    got = get(d)
    assert (got.status, got.received_amount, got.tx_hash) == (Status.DETECTED, "7", None)


def test_balance_fallback_waits_until_caught_up(chain):
    d = make_deposit(asset="TRX", expected=None)
    chain.balances[("TDeposit0", "TRX")] = 7
    chain.pay("TDeposit0", "TRX", 7, "tx1", block=chain.head + 60)
    chain.head += 60   # more than one loop's worth of blocks behind
    tick(chain, loops=0)
    assert get(d).status == Status.PENDING   # balance not trusted while behind
    tick(chain, loops=1)
    assert get(d).received_amount == "7"     # found by the scan, counted once


def test_cursor_is_per_chain(chain):
    other = StubScanner("bsc-testnet", head=5000)
    tick(other)
    assert get_counter(get_db(), "last_block:tron-nile") == chain.head
    assert get_counter(get_db(), "last_block:bsc-testnet") == 5000


def test_scan_is_capped_per_loop(chain):
    make_deposit()
    chain.head += 120
    tick(chain)
    start, end = chain.scanned[-1]
    assert end - start + 1 == core.MAX_BLOCKS_PER_LOOP


def test_watcher_records_head_and_heartbeat(chain):
    chain.head += 3
    tick(chain)
    assert get_counter(get_db(), "head:tron-nile") == chain.head
    assert get_counter(get_db(), "heartbeat:tron-nile") > 0


def test_every_status_change_is_an_event_for_the_project(chain):
    d = make_deposit(expected=10_000_000)
    chain.pay("TDeposit0", "USDT", 4_000_000, "tx1", block=chain.head + 1)
    advance(chain, 1)
    advance(chain, CONFIRMATIONS)
    close_window(d)
    tick(chain)
    assert event_types() == ["deposit.detected", "deposit.partial", "deposit.review"]
    last = get_db().events.find_one({"seq": 3})
    assert last["deposit"]["internal_status"] == "review" and last["deposit"]["received_amount"] == "4000000"


def test_the_projects_own_tolerance_counts(chain, project):
    get_db().projects.update_one({"_id": project["_id"]}, {"$set": {"amount_tolerance_bps": 1000}})   # 10%
    d = make_deposit(expected=10_000_000)
    chain.pay("TDeposit0", "USDT", 9_000_000, "tx1", block=chain.head + 1)
    advance(chain, CONFIRMATIONS + 1)
    assert get(d).status == Status.CONFIRMED


def test_an_older_review_status_is_upgraded_on_start(mongo):
    from api.db import create_indexes

    mongo.deposits.insert_one({"_id": "old", "address": "0xold", "status": "needs_review"})
    mongo.events.insert_one({"_id": "evt", "project_id": "p", "seq": 1, "deposit": {"internal_status": "needs_review"}})
    create_indexes(mongo)
    create_indexes(mongo)   # idempotent
    assert mongo.deposits.find_one({"_id": "old"})["status"] == "review"
    assert mongo.events.find_one({"_id": "evt"})["deposit"]["internal_status"] == "review"


def _three_second_blocks(scanner, last, count):
    """Block times ending at `last` = now, one every 3 seconds, like Tron."""
    now = utcnow()
    for i in range(count):
        scanner.times[last - i] = now - timedelta(seconds=3 * i)


def test_far_behind_skips_blocks_from_before_the_oldest_deposit(chain):
    set_counter(get_db(), "last_block:tron-nile", 1_000)
    chain.head = 1_000 + 10_000                       # 10,000 blocks behind: about 8 hours of Tron
    _three_second_blocks(chain, chain.head, 10_001)
    d = make_deposit()                                # opened now
    pay_at = chain.head - 5
    chain.pay("TDeposit0", "USDT", 10_000_000, "tx1", block=pay_at)
    tick(chain)
    start, _ = chain.scanned[-1]
    # 10 minutes before the deposit is 200 blocks back; nothing older is read
    assert chain.head - 210 <= start <= chain.head - 195
    for _ in range(5):
        tick(chain)
    assert get(d).status == Status.DETECTED and get(d).received_amount == "10000000"


def test_far_behind_with_nothing_to_watch_skips_to_the_head(chain):
    set_counter(get_db(), "last_block:tron-nile", 1_000)
    chain.head = 50_000
    assert core.run_once(get_db(), chain, 1) is True
    assert get_counter(get_db(), "last_block:tron-nile") == chain.head


def test_far_behind_without_block_times_reads_every_block(chain):
    set_counter(get_db(), "last_block:tron-nile", 1_000)
    chain.head = 5_000                                # no times known: nothing is skipped
    make_deposit()
    assert core.run_once(get_db(), chain, 1) is False   # still behind, so no pause before the next pass
    assert chain.scanned[-1] == (1_001, 1_000 + core.MAX_BLOCKS_PER_LOOP)
