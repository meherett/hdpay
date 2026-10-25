from datetime import timedelta

import pytest

from factories import MASTER, TRON_TREASURY, make_project
from api.chains import TxStatus
from api.db import Deposit, Status, find_deposit, get_counter, get_db, utcnow
from sweeper.core import sweep_once as _sweep_once
from stubs import StubSweeper


@pytest.fixture(autouse=True)
def project(mongo):
    return make_project(mongo)


def sweep_once(sweeper):
    _sweep_once(get_db(), sweeper)


def confirmed_deposit(chain="tron-nile", status=Status.CONFIRMED, address="TDeposit0", received="10000000", account="1"):
    d = Deposit.new(
        project_id=get_db().projects.find_one()["_id"], project_number=48213, account_id=account, product=None,
        chain=chain, family="tron", asset="USDT", address=address,
        derivation_date=utcnow().date().isoformat(), index=0, path=f"m/48213'/195'/{account}/2026/10/21/0",
        expected_amount=None, received_amount=received, status=status, expires_at=utcnow() + timedelta(minutes=30),
    )
    d.insert(get_db())
    return d.id


def update(deposit_id, **fields):
    get_db().deposits.update_one({"_id": deposit_id}, {"$set": fields})


def get(deposit_id):
    return find_deposit(get_db(), deposit_id)


def test_native_style_sweep_is_marked_swept_once_it_lands():
    d = confirmed_deposit()
    s = StubSweeper()
    sweep_once(s)
    assert get(d).sweep_tx_hash == "sweep-0" and get(d).status == Status.CONFIRMED
    sweep_once(s)                                  # still pending: nothing new is sent
    assert s.sent == [("sweep", "sweep-0")]
    s.statuses["sweep-0"] = TxStatus.OK
    sweep_once(s)
    assert get(d).status == Status.SWEPT


def test_token_sweep_waits_for_fee_top_up():
    d = confirmed_deposit()
    s = StubSweeper(needs_fee=True)
    sweep_once(s)
    assert get(d).fee_tx_hash == "fee-0"
    sweep_once(s)                                  # top-up not landed yet
    assert s.sent == [("fee", "fee-0")]
    s.statuses["fee-0"] = TxStatus.OK
    sweep_once(s)
    got = get(d)
    assert (got.fee_tx_hash, got.sweep_tx_hash) == (None, "sweep-1")


def test_failed_sweep_is_held_not_resent():
    d = confirmed_deposit()
    s = StubSweeper()
    sweep_once(s)
    s.statuses["sweep-0"] = TxStatus.FAILED
    sweep_once(s)
    sweep_once(s)
    assert get(d).status == Status.CONFIRMED and "failed on chain" in get(d).sweep_hold
    assert s.sent == [("sweep", "sweep-0")]          # never sent again


def test_failed_top_up_is_held_not_resent():
    # the Sepolia loop: every top-up failed on chain and a new one went out every pass
    d = confirmed_deposit()
    s = StubSweeper(needs_fee=True)
    sweep_once(s)
    s.statuses["fee-0"] = TxStatus.FAILED
    s.needs_fee = True
    for _ in range(5):
        sweep_once(s)
    assert s.sent == [("fee", "fee-0")]
    assert "failed on chain" in get(d).sweep_hold


def test_held_deposit_does_not_block_others():
    held = confirmed_deposit()
    update(held, sweep_hold="test")
    s = StubSweeper()
    other = confirmed_deposit(address="TDeposit1", received="1", account="2")
    sweep_once(s)
    assert get(other).sweep_tx_hash == "sweep-0" and get(held).sweep_tx_hash is None


def test_only_confirmed_deposits_on_this_chain_are_swept():
    confirmed_deposit(status=Status.DETECTED)
    s = StubSweeper()
    sweep_once(s)
    assert s.sent == []


def test_sweep_records_time_and_heartbeat():
    d = confirmed_deposit()
    s = StubSweeper()
    sweep_once(s)
    s.statuses["sweep-0"] = TxStatus.OK
    sweep_once(s)
    assert get(d).status == Status.SWEPT and get(d).swept_at is not None
    assert get_counter(get_db(), "sweeper_heartbeat:tron-nile") > 0


def test_held_deposit_survives_restart_until_released():
    d = confirmed_deposit()
    update(d, sweep_hold="fee top-up failed on chain")
    StubSweeper()                                    # a "restarted" sweeper still skips it
    s = StubSweeper()
    sweep_once(s)
    assert s.sent == []
    update(d, sweep_hold=None)                       # released in the admin
    sweep_once(s)
    assert s.sent == [("sweep", "sweep-0")]


def test_funds_go_to_the_projects_treasury_and_the_project_hears_of_it(project):
    d = confirmed_deposit()
    s = StubSweeper()
    sweep_once(s)
    s.statuses["sweep-0"] = TxStatus.OK
    sweep_once(s)
    assert s.treasuries == [TRON_TREASURY]
    assert [e["type"] for e in get_db().events.find()] == ["deposit.swept"]
    assert get(d).status == Status.SWEPT


def test_a_project_without_a_treasury_waits(project):
    get_db().projects.update_one({"_id": project["_id"]}, {"$set": {"treasury": {"evm": None, "tron": None}}})
    d = confirmed_deposit()
    s = StubSweeper()
    sweep_once(s)
    assert s.sent == [] and get(d).status == Status.CONFIRMED


def test_a_deposit_whose_coin_left_the_chains_file_is_held_with_the_reason():
    d = confirmed_deposit()
    update(d, asset="USDC")                       # USDC is not listed for Tron Nile
    s = StubSweeper()
    sweep_once(s)                                 # no crash, nothing sent
    got = get(d)
    assert s.sent == [] and "its contract is not known" in got.sweep_hold


# ---- the master key must be the one the projects were made with

def test_a_database_without_project_keys_takes_any_master_key(mongo):
    from sweeper.core import check_master_key_matches

    mongo.projects.delete_many({})
    mongo.projects.insert_one({"_id": "new", "name": "New", "number": 7, "xpubs": {}})
    check_master_key_matches(mongo, MASTER)          # nothing to compare: starts


def test_the_projects_own_master_key_is_accepted(mongo):
    from sweeper.core import check_master_key_matches

    mongo.projects.delete_many({})
    make_project(mongo)                              # its xpubs come from MASTER
    check_master_key_matches(mongo, MASTER)


def test_another_master_key_is_refused_naming_the_project_and_both_settings(mongo):
    from hdwallet import HDWallet
    from hdwallet.cryptocurrencies import Ethereum
    from hdwallet.hds import BIP32HD
    from hdwallet.mnemonics import BIP39Mnemonic

    from sweeper.core import WrongMasterKey, check_master_key_matches

    other = HDWallet(cryptocurrency=Ethereum, hd=BIP32HD, network=Ethereum.NETWORKS.MAINNET).from_mnemonic(
        mnemonic=BIP39Mnemonic(mnemonic="legal winner thank year wave sausage worth useful legal winner thank yellow")).root_xprivate_key()
    mongo.projects.delete_many({})
    make_project(mongo, name="Shop")
    with pytest.raises(WrongMasterKey) as refused:
        check_master_key_matches(mongo, other)
    message = str(refused.value)
    assert "Shop" in message and "HDPAY_MONGODB_DB" in message and "HDPAY_ROOT_XPRV" in message
    assert other not in message                      # the key itself is never shown


def test_the_sweeper_stops_before_sweeping_or_issuing_keys(mongo, monkeypatch):
    from sweeper import core
    from sweeper.core import WrongMasterKey

    mongo.projects.delete_many({})
    make_project(mongo)
    mongo.projects.insert_one({"_id": "waiting", "name": "Waiting", "number": 9, "xpubs": {}, "status": "awaiting_keys"})
    s = StubSweeper()
    s.root_xprv = "xprv-of-another-wallet"
    monkeypatch.setattr(core, "check_master_key_matches",
                        lambda db, key: (_ for _ in ()).throw(WrongMasterKey("wrong key")))
    monkeypatch.setattr(core, "sweep_once", lambda db, sweeper: pytest.fail("swept with the wrong key"))
    with pytest.raises(WrongMasterKey):
        core.run(s)
    assert mongo.projects.find_one({"_id": "waiting"})["xpubs"] == {}   # no keys handed out


@pytest.mark.parametrize("key, problem", [("", "not set"), ("not-a-key", "not a valid extended key")])
def test_the_key_is_checked_to_be_a_root_key_before_the_projects(mongo, key, problem):
    from sweeper.core import WrongMasterKey, check_master_key_matches

    mongo.projects.delete_many({})                   # even with no projects, a bad key is refused
    with pytest.raises(WrongMasterKey, match=problem):
        check_master_key_matches(mongo, key)


def test_a_child_key_is_refused_as_not_root_even_before_projects_are_compared(mongo, monkeypatch):
    from hdwallet import HDWallet
    from hdwallet.cryptocurrencies import Ethereum
    from hdwallet.derivations import CustomDerivation
    from hdwallet.hds import BIP32HD

    from sweeper import core

    child = HDWallet(cryptocurrency=Ethereum, hd=BIP32HD, network=Ethereum.NETWORKS.MAINNET) \
        .from_xprivate_key(xprivate_key=MASTER).from_derivation(derivation=CustomDerivation(path="m/1'")).xprivate_key()
    mongo.projects.delete_many({})
    make_project(mongo)
    monkeypatch.setattr(core, "project_xpub", lambda *a: pytest.fail("projects compared before the root check"))
    with pytest.raises(core.WrongMasterKey, match="root key"):
        core.check_master_key_matches(mongo, child)
