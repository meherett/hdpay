"""Deadlines judged by the chain's clock, and support checks: a watcher that was stopped and catches
up later judges each payment by the time of its block, and support can have an address rescanned."""

from datetime import timedelta

import pytest

from factories import make_project
from api.db import Deposit, Status, find_deposit, get_db, set_counter, utcnow
from watcher import core
from stubs import StubScanner

CONFIRMATIONS = 20   # tron-nile
BLOCK = timedelta(seconds=3)


@pytest.fixture(autouse=True)
def project(mongo):
    return make_project(mongo)


@pytest.fixture
def chain():
    """A chain whose blocks have times: block n is (head - n) * 3 seconds before now."""
    s = StubScanner(head=10_000)
    timed(s)
    return s


def timed(s):
    now = utcnow()
    s.times = {n: now - (s.head - n) * BLOCK for n in range(1, s.head + 400)}


def at(s, block):
    return s.times[block]


def make_deposit(s, opened_block, window_blocks=600, **fields):
    """A deposit opened at `opened_block`, with a payment window of `window_blocks` (30 minutes)."""
    opened = at(s, opened_block)
    d = Deposit.new(
        project_id=get_db().projects.find_one()["_id"], project_number=48213, account_id="1", product=None,
        chain="tron-nile", family="tron", asset="USDT", address="TDeposit0", derivation_date=opened.date().isoformat(),
        index=0, path="m/48213'/195'/1/2026/10/21/0", expected_amount="10000000",
        created_at=opened, expires_at=opened + window_blocks * BLOCK, window_ends_at=opened + window_blocks * BLOCK,
        **fields,
    )
    d.insert(get_db())
    return d.id


def get(deposit_id):
    return find_deposit(get_db(), deposit_id)


def tick(s, loops=1):
    return core.run_once(get_db(), s, loops)


def catch_up(s, loops=1):
    for _ in range(400):
        if tick(s, loops):
            return


def event_types():
    return [e["type"] for e in get_db().events.find().sort("seq", 1)]


def stopped_at(s, block):
    """The watcher last read `block`: it was stopped there and starts again now."""
    set_counter(get_db(), "last_block:tron-nile", block)


def ask_for_check(deposit_id):
    get_db().deposits.update_one(
        {"_id": deposit_id}, {"$set": {"check": {"status": "waiting", "requested_at": utcnow(), "requested_by": "Ada"}}}
    )


# ---- a watcher that was stopped judges payments by their block's time


def test_paid_on_time_while_the_watcher_was_stopped_is_not_late(chain):
    # opened 9,000 blocks (7.5 hours) ago with a 30 minute window; paid 10 minutes after opening;
    # the watcher stopped just after the deposit opened and starts again now, long after the window
    d = make_deposit(chain, opened_block=1_000)
    chain.pay("TDeposit0", "USDT", 10_000_000, "tx1", block=1_200)
    stopped_at(chain, 1_010)
    catch_up(chain)
    got = get(d)
    assert (got.status, got.late, got.received_amount) == (Status.CONFIRMED, False, "10000000")
    assert "deposit.expired" not in event_types()
    assert [p["tx_hash"] for p in got.payments] == ["tx1"]


def test_the_rest_of_an_underpayment_sent_in_time_while_the_watcher_was_stopped_is_credited(chain):
    # partial with 4 of 10, the rest due by block 1,600; the rest came at block 1,500; the watcher
    # stopped at 1,400 and starts again now: before, the clock sent it to review and it was never counted
    d = make_deposit(
        chain, opened_block=1_000, status=Status.PARTIAL.value, received_amount="4000000", confirmed_amount="4000000",
        tx_hash="tx1", block_number=1_100,
    )
    get_db().deposits.update_one({"_id": d}, {"$set": {"expires_at": at(chain, 1_600)}})
    chain.pay("TDeposit0", "USDT", 6_000_000, "tx2", block=1_500)
    stopped_at(chain, 1_400)
    catch_up(chain)
    got = get(d)
    assert (got.status, got.received_amount) == (Status.CONFIRMED, "10000000")
    assert "deposit.review" not in event_types()


def test_a_payment_really_after_the_window_is_still_late(chain):
    d = make_deposit(chain, opened_block=1_000)              # window ends at block 1,600
    chain.pay("TDeposit0", "USDT", 10_000_000, "tx1", block=2_000)
    stopped_at(chain, 1_010)
    catch_up(chain)
    got = get(d)
    assert (got.status, got.late) == (Status.CONFIRMED, True)
    assert event_types().index("deposit.expired") < event_types().index("deposit.confirmed")


def test_the_rest_really_after_the_remainder_window_still_goes_to_review(chain):
    d = make_deposit(
        chain, opened_block=1_000, status=Status.PARTIAL.value, received_amount="4000000", confirmed_amount="4000000",
        tx_hash="tx1", block_number=1_100,
    )
    get_db().deposits.update_one({"_id": d}, {"$set": {"expires_at": at(chain, 1_600)}})
    chain.pay("TDeposit0", "USDT", 6_000_000, "tx2", block=1_700)
    stopped_at(chain, 1_400)
    catch_up(chain)
    assert get(d).status == Status.REVIEW


# ---- support checks


def test_a_check_credits_a_deposit_sent_to_review_although_the_rest_came_in_time(chain):
    # last week's mistake: the watcher was stopped, the clock sent it to review, and its scan has
    # long passed the block with the rest, so only a check can find it
    d = make_deposit(
        chain, opened_block=9_700, status=Status.REVIEW.value, received_amount="4000000", confirmed_amount="4000000",
        tx_hash="tx1", block_number=9_750,                       # counted before payments were listed
    )
    get_db().deposits.update_one({"_id": d}, {"$set": {"expires_at": at(chain, 9_900)}})
    chain.pay("TDeposit0", "USDT", 4_000_000, "tx1", block=9_750)
    chain.pay("TDeposit0", "USDT", 6_000_000, "tx2", block=9_850)   # the rest, before 9,900: on time
    stopped_at(chain, chain.head - 1)
    ask_for_check(d)
    tick(chain)
    got = get(d)
    assert (got.status, got.received_amount) == (Status.DETECTED, "10000000")   # the first 4 not counted again
    assert (got.check["status"], got.check["counted"]) == ("done", "6000000")
    assert sorted(p["tx_hash"] for p in got.payments) == ["tx1", "tx2"]
    chain.head += CONFIRMATIONS
    timed(chain)
    catch_up(chain)
    assert get(d).status == Status.CONFIRMED
    assert "deposit.confirmed" in event_types()


def test_a_check_leaves_a_deposit_whose_rest_was_late_for_support(chain):
    d = make_deposit(
        chain, opened_block=9_700, status=Status.REVIEW.value, received_amount="4000000", confirmed_amount="4000000",
        tx_hash="tx1", block_number=9_750,
    )
    get_db().deposits.update_one({"_id": d}, {"$set": {"expires_at": at(chain, 9_800)}})
    chain.pay("TDeposit0", "USDT", 4_000_000, "tx1", block=9_750)
    chain.pay("TDeposit0", "USDT", 6_000_000, "tx2", block=9_850)   # after 9,800: late
    stopped_at(chain, chain.head - 1)
    ask_for_check(d)
    tick(chain)
    got = get(d)
    assert (got.status, got.received_amount) == (Status.REVIEW, "10000000")   # counted, support decides


def test_a_check_clears_a_late_mark_the_clock_put_on_an_on_time_payment(chain):
    d = make_deposit(chain, opened_block=9_700, late=True, status=Status.EXPIRED.value)
    chain.pay("TDeposit0", "USDT", 10_000_000, "tx1", block=9_750)   # inside the window, which ends at 10,300
    stopped_at(chain, chain.head - 1)                                 # the scan already passed it
    ask_for_check(d)
    tick(chain)
    got = get(d)
    # long since confirmed on the chain, so paid in the same pass, and not late
    assert (got.status, got.late, got.received_amount) == (Status.CONFIRMED, False, "10000000")


def test_checking_twice_counts_once(chain):
    d = make_deposit(chain, opened_block=9_700)
    chain.pay("TDeposit0", "USDT", 10_000_000, "tx1", block=9_750)
    stopped_at(chain, chain.head - 1)
    ask_for_check(d)
    tick(chain)
    ask_for_check(d)
    tick(chain)
    got = get(d)
    assert (got.received_amount, got.check["counted"]) == ("10000000", "0")


def test_a_check_and_the_scan_finding_the_same_payment_count_it_once(chain):
    d = make_deposit(chain, opened_block=9_700)
    chain.pay("TDeposit0", "USDT", 10_000_000, "tx1", block=chain.head + 1)
    stopped_at(chain, chain.head - 1)
    chain.head += 2
    timed(chain)
    ask_for_check(d)
    tick(chain)                          # the scan counts it, then the check finds it again
    assert get(d).received_amount == "10000000"


def test_a_check_finds_coins_only_the_balance_shows(chain):
    # TRX sent through a contract: no transfer to find, only the balance
    d = make_deposit(chain, opened_block=9_700)
    chain.balances[("TDeposit0", "USDT")] = 10_000_000
    stopped_at(chain, chain.head - 1)
    ask_for_check(d)
    tick(chain)
    got = get(d)
    assert (got.status, got.received_amount, got.check["from_balance"]) == (Status.DETECTED, "10000000", "10000000")
    assert got.payments[0]["source"] == "balance"


# ---- a balance read that only knows "now" (Tron) never counts a payment twice


class NowOnlyChain(StubScanner):
    """Like Tron: the balance can only be read now, and covers blocks the scan has not reached."""

    def balance_covering(self, address, asset, block, contract=None):
        balance = self.balances.get((address, asset))
        return None if balance is None else (balance, self.head + 3)


def test_a_balance_read_ahead_of_the_scan_is_not_counted_again_by_it():
    s = NowOnlyChain(head=10_000)
    timed(s)
    d = make_deposit(s, opened_block=9_990)
    stopped_at(s, s.head - 1)
    s.pay("TDeposit0", "USDT", 10_000_000, "tx1", block=s.head + 2)   # in the balance, not scanned yet
    s.balances[("TDeposit0", "USDT")] = 10_000_000
    tick(s, loops=core.BALANCE_CHECK_EVERY_LOOPS)
    assert get(d).received_amount == "10000000"
    s.head += 5
    timed(s)
    tick(s)                                    # the scan reaches the transfer: already in the balance
    assert get(d).received_amount == "10000000"


def test_a_long_check_is_read_a_slice_per_pass_and_finishes(chain, monkeypatch):
    monkeypatch.setattr(core, "CHECK_SECONDS_PER_PASS", 0)   # one slice per pass
    monkeypatch.setattr(core, "CHECK_CHUNK_BLOCKS", 100)
    d = make_deposit(chain, opened_block=9_500, late=True, status=Status.EXPIRED.value)   # 500 blocks to read
    chain.pay("TDeposit0", "USDT", 10_000_000, "tx1", block=9_850)
    stopped_at(chain, chain.head - 1)
    ask_for_check(d)
    tick(chain)
    first = get(d).check
    assert first["status"] == "waiting" and first["next_block"] < chain.head   # part read, the place kept
    for _ in range(10):
        tick(chain)
    got = get(d)
    assert (got.check["status"], got.received_amount, got.late) == ("done", "10000000", False)


# ---- expired deposits that never received anything are deleted after HDPAY_EXPIRED_DELETE_AFTER_HOURS

def expired_long_ago(s, hours, **fields):
    """A deposit whose window ended `hours` ago with nothing received."""
    deposit_id = make_deposit(s, s.head - 50, status=Status.EXPIRED, **fields)
    get_db().deposits.update_one({"_id": deposit_id}, {"$set": {"expires_at": utcnow() - timedelta(hours=hours)}})
    return deposit_id


def test_an_expired_unpaid_deposit_is_deleted_after_the_set_hours(chain, monkeypatch):
    monkeypatch.setattr(core.settings, "expired_delete_after_hours", 24)
    chain.balances[("TDeposit0", "USDT")] = 0
    old = expired_long_ago(chain, 25)
    catch_up(chain)
    assert get(old) is None and "deposit.deleted" in event_types()


def test_a_recent_one_is_kept(chain, monkeypatch):
    monkeypatch.setattr(core.settings, "expired_delete_after_hours", 24)
    chain.balances[("TDeposit0", "USDT")] = 0
    recent = expired_long_ago(chain, 23)
    catch_up(chain)
    assert get(recent).status == Status.EXPIRED


def test_zero_hours_keeps_them_all(chain, monkeypatch):
    monkeypatch.setattr(core.settings, "expired_delete_after_hours", 0)
    chain.balances[("TDeposit0", "USDT")] = 0
    old = expired_long_ago(chain, 1000)
    catch_up(chain)
    assert get(old) is not None


def test_money_found_at_the_last_look_is_counted_not_deleted(chain, monkeypatch):
    monkeypatch.setattr(core.settings, "expired_delete_after_hours", 24)
    chain.balances[("TDeposit0", "USDT")] = 10_000_000   # sent while nobody was looking
    old = expired_long_ago(chain, 25)
    catch_up(chain)
    d = get(old)
    assert d is not None and d.status == Status.DETECTED and d.received_amount == "10000000"


def test_a_balance_that_cannot_be_read_keeps_it(chain, monkeypatch):
    monkeypatch.setattr(core.settings, "expired_delete_after_hours", 24)
    old = expired_long_ago(chain, 25)          # no balance known: the stub cannot say
    catch_up(chain)
    assert get(old) is not None


def test_by_default_an_expired_deposit_is_kept_for_15_days(chain, monkeypatch):
    from api.config import Settings

    assert Settings.model_fields["expired_delete_after_hours"].default == 360
    chain.balances[("TDeposit0", "USDT")] = 0
    monkeypatch.setattr(core.settings, "expired_delete_after_hours", 360)
    kept = expired_long_ago(chain, 14 * 24)
    catch_up(chain)
    assert get(kept) is not None                      # 14 days: still kept, still watched
    get_db().deposits.update_one({"_id": kept}, {"$set": {"expires_at": utcnow() - timedelta(days=15, minutes=1)}})
    catch_up(chain, loops=2)
    assert get(kept) is None                          # 15 days: deleted


# ---- a deposit the project cancelled is still watched

def cancelled_at_block(s, opened_block, cancelled_block):
    """A deposit opened at `opened_block` and cancelled by its project at `cancelled_block`."""
    deposit_id = make_deposit(s, opened_block)
    at_cancel = at(s, cancelled_block)
    get_db().deposits.update_one({"_id": deposit_id}, {"$set": {
        "status": Status.CANCELLED.value, "expires_at": at_cancel, "cancelled_at": at_cancel}})
    return deposit_id


def test_a_payment_after_the_cancel_is_counted_late(chain):
    d = cancelled_at_block(chain, chain.head - 100, chain.head - 80)
    stopped_at(chain, chain.head - 70)
    chain.pay("TDeposit0", "USDT", 10_000_000, "after", block=chain.head - 50)
    catch_up(chain)
    got = get(d)
    assert (got.status, got.late, got.received_amount) == (Status.CONFIRMED, True, "10000000")   # late, still credited
    assert "deposit.confirmed" in event_types()


def test_a_payment_sent_before_the_cancel_is_on_time(chain):
    d = cancelled_at_block(chain, chain.head - 100, chain.head - 80)
    stopped_at(chain, chain.head - 95)
    chain.pay("TDeposit0", "USDT", 10_000_000, "before", block=chain.head - 90)   # on its way when cancelled
    catch_up(chain)
    got = get(d)
    assert (got.status, got.late) == (Status.CONFIRMED, False)


def test_a_failed_payment_returns_it_to_cancelled(chain):
    d = cancelled_at_block(chain, chain.head - 100, chain.head - 80)
    stopped_at(chain, chain.head - 70)
    chain.pay("TDeposit0", "USDT", 10_000_000, "reorged", block=chain.head)   # not confirmed yet
    catch_up(chain)
    assert get(d).status == Status.DETECTED
    chain.statuses["reorged"] = __import__("api.chains", fromlist=["TxStatus"]).TxStatus.FAILED
    chain.head += CONFIRMATIONS
    timed(chain)
    catch_up(chain)
    assert get(d).status == Status.CANCELLED and get(d).received_amount == "0"


def test_a_cancelled_unpaid_deposit_is_deleted_like_an_expired_one(chain, monkeypatch):
    monkeypatch.setattr(core.settings, "expired_delete_after_hours", 360)
    chain.balances[("TDeposit0", "USDT")] = 0
    d = cancelled_at_block(chain, chain.head - 100, chain.head - 80)
    get_db().deposits.update_one({"_id": d}, {"$set": {"expires_at": utcnow() - timedelta(days=15, minutes=1)}})
    catch_up(chain)
    assert get(d) is None and "deposit.deleted" in event_types()
