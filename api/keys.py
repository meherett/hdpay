"""
Key derivation. Every project has its own hardened branch, then one hardened node per chain family:

    m/{project}'/60'      evm: 0x addresses, identical on Ethereum, Sepolia and BNB Smart Chain
    m/{project}'/195'     tron: T... addresses, identical on Tron mainnet and Nile

The project number is chosen at random when the project is created. Below the coin node every level
is non-hardened, so the API derives addresses from that node's xpub alone:

    m/{project}'/{coin}'/{account}/{seconds}/{microseconds}   deposit
    m/{project}'/{coin}'                                      the project's fee funder (the coin node itself)

A deposit's path is the moment its address was made: {seconds} since 2026-01-01 00:00 UTC
(PATH_EPOCH; a path level holds up to 2^31 - 1, which lasts until 2094-01-19), and {microseconds}
within that second (0 to 999999). Every address is for one moment only, and its path tells when:

    m/48213'/195'/42/25707922/123456   account 42, 2026-10-25 13:05:22.123456 UTC

Deposits from before 0.3.0 have m/{project}'/{coin}'/{account}/{year}/{month}/{day}/{index}. Each
deposit keeps the path it was made with and the sweeper signs with that, so both kinds sweep.

The hardened project level keeps projects apart: one project's keys reveal nothing about another's.
It also means a project's xpubs can only be made from the master key, so they are exported once, on
the sweeper host, with `python -m api.keys project <number>`, and pasted into the admin.

The API never sees a private key; the sweepers hold the master key (HDPAY_MASTER_XPRV).
"""

import argparse
import getpass
import os
import sys
from datetime import datetime, timedelta

from hdwallet import HDWallet
from hdwallet.cryptocurrencies import Ethereum, Tron
from hdwallet.derivations import CustomDerivation
from hdwallet.hds import BIP32HD
from hdwallet.mnemonics import BIP39_MNEMONIC_LANGUAGES, BIP39_MNEMONIC_WORDS, BIP39Mnemonic

from .chains import Family

_CRYPTOCURRENCIES = {
    Family.EVM: Ethereum,
    Family.TRON: Tron,
}
PROJECT_KEY_DEPTH = 2             # a project's xpub is its m/{project}'/{coin}' node
MAX_INDEX = 2**31 - 1             # largest BIP32 index, hardened or not
HARDENED = 2**31


def _wallet(family: Family) -> HDWallet:
    cryptocurrency = _CRYPTOCURRENCIES[family]
    return HDWallet(cryptocurrency=cryptocurrency, hd=BIP32HD, network=cryptocurrency.NETWORKS.MAINNET)


def _check_index(value: int, what: str) -> None:
    if not 0 <= value <= MAX_INDEX:
        raise ValueError(f"{what} must be from 0 to {MAX_INDEX}")


def coin_path(number: int, family: Family) -> str:
    """The project's node for a chain family, m/{project}'/{coin}'."""
    _check_index(number, "project number")
    return f"m/{number}'/{_CRYPTOCURRENCIES[family].COIN_TYPE}'"


PATH_EPOCH = datetime(2026, 1, 1)   # naive UTC: a deposit path counts its seconds from here


def path_moment(at: datetime) -> tuple[int, int]:
    """The two path levels for a moment (naive UTC): seconds since PATH_EPOCH, and microseconds."""
    since = at.replace(tzinfo=None) - PATH_EPOCH
    seconds = since.days * 86_400 + since.seconds
    if seconds < 0:
        raise ValueError(f"a deposit path counts from {PATH_EPOCH.date()}: {at} is before it")
    _check_index(seconds, "seconds since the path epoch")
    return seconds, since.microseconds


def moment_of(seconds: int, microseconds: int) -> datetime:
    """The moment (naive UTC) a deposit path's two last levels stand for."""
    return PATH_EPOCH + timedelta(seconds=seconds, microseconds=microseconds)


def relative_path(account_id: int, at: datetime) -> str:
    _check_index(account_id, "account id")
    seconds, microseconds = path_moment(at)
    return f"m/{account_id}/{seconds}/{microseconds}"


def full_path(number: int, family: Family, account_id: int, at: datetime) -> str:
    return coin_path(number, family) + relative_path(account_id, at)[1:]


# ---- the master key (sweeper host)

def check_master_key(xprv: str) -> None:
    """Refuse anything but a root private key: every project is derived below it."""
    try:
        node = _wallet(Family.EVM).from_xprivate_key(xprivate_key=xprv)
        depth = node.depth()
    except Exception as exc:
        raise RuntimeError(f"HDPAY_MASTER_XPRV is not a valid extended private key: {exc}") from exc
    if depth != 0:
        raise RuntimeError(f"HDPAY_MASTER_XPRV must be the root key (depth 0), got depth {depth}")


def _from_master(family: Family, master_xprv: str, path: str) -> HDWallet:
    return _wallet(family).from_xprivate_key(xprivate_key=master_xprv).from_derivation(
        derivation=CustomDerivation(path=path)
    )


def project_xpub(master_xprv: str, number: int, family: Family) -> str:
    """The watch-only key the API needs for a project's addresses on a chain family."""
    return _from_master(family, master_xprv, coin_path(number, family)).xpublic_key()


def private_key_for_path(family: Family, master_xprv: str, path: str) -> str:
    """Hex private key (no 0x prefix) for a full path stored with a deposit. Sweeper only.

    The sweeper uses the stored path rather than rebuilding it, so a deposit keeps its key even if
    the path layout changes later.
    """
    if not path.startswith("m/"):
        raise ValueError(f"{path} is not a full derivation path")
    return _from_master(family, master_xprv, path).private_key()


# ---- a project's xpubs (API)

def check_project_xpub(family: Family, xpub: str) -> None:
    """Refuse a key from the wrong level or chain family: its addresses would silently differ."""
    if not xpub.startswith("xpub"):
        raise ValueError(f"the {family} key must be an xpub (a public key), not {xpub[:4]}...")
    try:
        node = _wallet(family).from_xpublic_key(xpublic_key=xpub, strict=False)
        depth, index = node.depth(), node.index()
    except Exception as exc:
        raise ValueError(f"the {family} key is not a valid xpub") from exc
    coin = _CRYPTOCURRENCIES[family].COIN_TYPE
    if depth != PROJECT_KEY_DEPTH or index != HARDENED + coin:
        raise ValueError(
            f"the {family} key must be the project's m/<project>'/{coin}' node; "
            "export it with: python -m api.keys project <number>"
        )


def address_at(family: Family, xpub: str, account_id: int, at: datetime) -> str:
    """Deposit address for the moment `at`, derived watch-only from the project's xpub for the chain family."""
    hd = (
        _wallet(family)
        .from_xpublic_key(xpublic_key=xpub, strict=False)
        .from_derivation(derivation=CustomDerivation(path=relative_path(account_id, at)))
    )
    return hd.address()


# ---- fee funders: one per project and chain family, at the coin node itself
#
# Before 0.4.0 a funder was m/{project}'/{coin}'/{year}, a new one each UTC year. `python -m api.keys
# project <number>` lists those too, so what one still holds can be checked and moved by hand.

def funder_path(number: int, family: Family) -> str:
    return coin_path(number, family)


def funder_address(family: Family, xpub: str) -> str:
    """The project's fee funder: the address of the coin node its xpub is for."""
    return _wallet(family).from_xpublic_key(xpublic_key=xpub, strict=False).address()


def funder_private_key(family: Family, master_xprv: str, number: int) -> str:
    return _from_master(family, master_xprv, funder_path(number, family)).private_key()


OLD_FUNDER_YEARS = (2025, 2026)   # the years a funder from before 0.4.0 can be for


def old_funder_address(family: Family, xpub: str, year: int) -> str:
    """A fee funder from before 0.4.0, m/{project}'/{coin}'/{year}, to check what it still holds."""
    hd = _wallet(family).from_xpublic_key(xpublic_key=xpub, strict=False)
    return hd.from_derivation(derivation=CustomDerivation(path=f"m/{year}")).address()


# ---- command line, on the host that holds the master key

def _master_from_environment() -> str:
    from .config import settings

    if not settings.master_xprv:
        raise SystemExit("HDPAY_MASTER_XPRV is not set: put it in envs/sweeper.env (python -m api.keys master)")
    check_master_key(settings.master_xprv)
    return settings.master_xprv


def _save_master(key: str) -> None:
    """Write the key into envs/sweeper.env (UTF-8, whatever the shell), never over another key."""
    from dotenv import dotenv_values

    from .config import ENVS

    path = ENVS / "sweeper.env"
    current = dotenv_values(path).get("HDPAY_MASTER_XPRV") if path.exists() else None
    if current and current != key:
        raise SystemExit(f"{path} already holds a different master key; remove that line first if you mean to replace it")
    template = ENVS / "sweeper.env.example"
    text = template.read_text(encoding="utf-8") if template.exists() else "HDPAY_MASTER_XPRV="
    lines = [f"HDPAY_MASTER_XPRV={key}" if line.startswith("HDPAY_MASTER_XPRV=") else line for line in text.splitlines()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Saved the master key to {path}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m api.keys", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    master = commands.add_parser(
        "master",
        help="the root key (HDPAY_MASTER_XPRV) for a recovery phrase, or for a new 24-word one",
    )
    master.add_argument(
        "--save", action="store_true",
        help="ask for the phrase (hidden; empty for a new wallet) and write the key to envs/sweeper.env",
    )
    project = commands.add_parser("project", help="print a project's xpubs, to paste into the admin")
    project.add_argument("number", type=int, help="the project number the admin shows")
    args = parser.parse_args()

    if args.command == "master":
        given = os.environ.get("MNEMONIC")
        if given is None and args.save:
            given = getpass.getpass("Recovery phrase (hidden; leave empty to create a new wallet): ")
        given = " ".join((given or "").lower().split())
        if given and not BIP39Mnemonic.is_valid(given):
            raise SystemExit("That is not a valid BIP39 recovery phrase: check the words and their order")
        phrase = given or BIP39Mnemonic.from_words(
            words=BIP39_MNEMONIC_WORDS.TWENTY_FOUR, language=BIP39_MNEMONIC_LANGUAGES.ENGLISH
        )
        key = _wallet(Family.EVM).from_mnemonic(mnemonic=BIP39Mnemonic(mnemonic=phrase)).root_xprivate_key()
        if not given:
            # on screen only (stderr): `> envs/sweeper.env` and --save store the key, never the phrase
            print(f"New recovery phrase: {phrase}", file=sys.stderr)
            print("Write it down and keep it offline. The services only need the key.", file=sys.stderr)
        if args.save:
            _save_master(key)
            return
        print(f"HDPAY_MASTER_XPRV={key}")
        if sys.stdout.isatty():
            print("# put this line in envs/sweeper.env: the sweepers need it, the API never does", file=sys.stderr)
        return

    master = _master_from_environment()
    print(f"# project {args.number}: paste these into the project's Keys step in the admin")
    for family in Family:
        xpub = project_xpub(master, args.number, family)
        print(f"{family}  {coin_path(args.number, family)}  {xpub}")
        print(f"      fee funder {funder_path(args.number, family)} = {funder_address(family, xpub)}")
        for year in OLD_FUNDER_YEARS:
            print(f"      old fee funder (before 0.4.0) {funder_path(args.number, family)}/{year} = "
                  f"{old_funder_address(family, xpub, year)}: move what it holds to the one above")


if __name__ == "__main__":
    main()
