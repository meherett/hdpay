from datetime import date

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
    funder_path,
    funder_private_key,
    private_key_for_path,
    project_xpub,
)

DAY = date(2026, 10, 21)


def test_the_project_comes_before_the_coin_type():
    assert coin_path(NUMBER, Family.EVM) == "m/48213'/60'"
    assert full_path(NUMBER, Family.TRON, DAY, 42, 0) == "m/48213'/195'/42/2026/10/21/0"
    assert funder_path(NUMBER, Family.EVM, 2026) == "m/48213'/60'/2026"


@pytest.mark.parametrize("family, cryptocurrency", [(Family.EVM, Ethereum), (Family.TRON, Tron)])
def test_watch_only_addresses_match_the_mnemonic(family, cryptocurrency):
    xpub = project_xpub(MASTER, NUMBER, family)
    check_project_xpub(family, xpub)
    path = full_path(NUMBER, family, DAY, 42, 7)
    assert address_at(family, xpub, DAY, 42, 7) == wallet_address(cryptocurrency, path)
    assert funder_address(family, xpub, 2026) == wallet_address(cryptocurrency, funder_path(NUMBER, family, 2026))


def test_projects_get_different_addresses():
    one = address_at(Family.EVM, project_xpub(MASTER, 1, Family.EVM), DAY, 42, 0)
    two = address_at(Family.EVM, project_xpub(MASTER, 2, Family.EVM), DAY, 42, 0)
    assert one != two


def test_sweep_and_funder_keys_come_from_the_master_key():
    from eth_account import Account

    path = full_path(NUMBER, Family.EVM, DAY, 42, 0)
    xpub = project_xpub(MASTER, NUMBER, Family.EVM)
    assert Account.from_key(private_key_for_path(Family.EVM, MASTER, path)).address == address_at(Family.EVM, xpub, DAY, 42, 0)
    assert Account.from_key(funder_private_key(Family.EVM, MASTER, NUMBER, 2026)).address == funder_address(Family.EVM, xpub, 2026)


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


@pytest.mark.parametrize("account_id, index", [(-1, 0), (2**31, 0), (0, 2**31)])
def test_path_levels_must_be_bip32_indexes(account_id, index):
    with pytest.raises(ValueError):
        full_path(NUMBER, Family.EVM, DAY, account_id, index)
