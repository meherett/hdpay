from datetime import datetime

import pytest
from hdwallet.cryptocurrencies import Ethereum, Tron

from factories import MASTER, NUMBER, wallet_address
from api.chains import Family
from api.keys import (
    address_at,
    check_master_key,
    check_project_xpub,
    coin_path,
    full_path,
    funder_address,
    moment_of,
    path_moment,
    funder_path,
    funder_private_key,
    private_key_for_path,
    project_xpub,
)

AT = datetime(2026, 10, 25, 13, 5, 22, 123456)   # 25707922 s and 123456 µs after 2026-01-01


def test_the_project_comes_before_the_coin_type():
    assert coin_path(NUMBER, Family.EVM) == "m/48213'/60'"
    assert full_path(NUMBER, Family.TRON, 42, AT) == "m/48213'/195'/42/25707922/123456"
    assert funder_path(NUMBER, Family.EVM) == "m/48213'/60'"


@pytest.mark.parametrize("family, cryptocurrency", [(Family.EVM, Ethereum), (Family.TRON, Tron)])
def test_watch_only_addresses_match_the_mnemonic(family, cryptocurrency):
    xpub = project_xpub(MASTER, NUMBER, family)
    check_project_xpub(family, xpub)
    path = full_path(NUMBER, family, 42, AT)
    assert address_at(family, xpub, 42, AT) == wallet_address(cryptocurrency, path)
    assert funder_address(family, xpub) == wallet_address(cryptocurrency, funder_path(NUMBER, family))


def test_projects_get_different_addresses():
    one = address_at(Family.EVM, project_xpub(MASTER, 1, Family.EVM), 42, AT)
    two = address_at(Family.EVM, project_xpub(MASTER, 2, Family.EVM), 42, AT)
    assert one != two


def test_sweep_and_funder_keys_come_from_the_master_key():
    from eth_account import Account

    path = full_path(NUMBER, Family.EVM, 42, AT)
    xpub = project_xpub(MASTER, NUMBER, Family.EVM)
    assert Account.from_key(private_key_for_path(Family.EVM, MASTER, path)).address == address_at(Family.EVM, xpub, 42, AT)
    assert Account.from_key(funder_private_key(Family.EVM, MASTER, NUMBER)).address == funder_address(Family.EVM, xpub)


def test_only_a_root_key_is_a_master_key():
    check_master_key(MASTER)
    from hdwallet import HDWallet
    from hdwallet.hds import BIP32HD

    child = HDWallet(cryptocurrency=Ethereum, hd=BIP32HD, network=Ethereum.NETWORKS.MAINNET) \
        .from_xprivate_key(xprivate_key=MASTER).from_derivation(derivation=__import__("hdwallet.derivations", fromlist=["CustomDerivation"]).CustomDerivation(path="m/1'"))
    with pytest.raises(RuntimeError, match="root key"):
        check_master_key(child.xprivate_key())


def test_a_key_from_the_wrong_family_or_level_is_refused():
    with pytest.raises(ValueError, match="m/<project>'/60'"):
        check_project_xpub(Family.EVM, project_xpub(MASTER, NUMBER, Family.TRON))
    with pytest.raises(ValueError, match="an xpub"):
        check_project_xpub(Family.EVM, MASTER)
    with pytest.raises(ValueError, match="not a valid xpub"):
        check_project_xpub(Family.EVM, "xpub-nonsense")


@pytest.mark.parametrize("account_id, at", [
    (-1, AT), (2**31, AT),
    (0, datetime(2025, 12, 31, 23, 59, 59)),       # before the path epoch
    (0, datetime(2094, 1, 19, 3, 14, 8)),          # seconds past 2^31 - 1
])
def test_path_levels_must_be_bip32_indexes(account_id, at):
    with pytest.raises(ValueError):
        full_path(NUMBER, Family.EVM, account_id, at)


def test_a_path_is_its_moment_to_the_microsecond():
    assert path_moment(AT) == (25707922, 123456)
    assert moment_of(*path_moment(AT)) == AT
    later = AT.replace(microsecond=123457)                 # one microsecond on: another address
    assert full_path(NUMBER, Family.TRON, 42, later) == "m/48213'/195'/42/25707922/123457"
    xpub = project_xpub(MASTER, NUMBER, Family.TRON)
    assert address_at(Family.TRON, xpub, 42, AT) != address_at(Family.TRON, xpub, 42, later)
    assert full_path(NUMBER, Family.EVM, 0, datetime(2094, 1, 19, 3, 14, 7)).endswith("/2147483647/0")   # the last one
