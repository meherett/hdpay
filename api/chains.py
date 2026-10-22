"""
Supported chains and assets, loaded from a YAML file (HDPAY_CHAINS_FILE, default chains.yaml,
or the committed chains.example.yaml while there is no chains.yaml). YAML so the file can carry
comments; a JSON file still loads, since JSON is YAML.

A deposit is for one chain and one asset. The asset is the chain's native symbol (ETH, BNB, TRX)
or a token symbol listed under that chain. Amounts are always in the asset's smallest unit.

Adding a token or a chain of a known family is an edit to the file, not to code. chains.yaml is
git-ignored, so its `rpc_url`s may carry access tokens; chains.example.yaml has public ones.
HDPAY_RPC_URL_<KEY> in the environment or envs/common.env overrides a file's `rpc_url` (dashes in the key
become underscores).

The file is followed while the processes run: an edit is picked up within a second, and an
invalid edit is logged and ignored, keeping the chains loaded before it.
"""

import logging
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config import env_value, settings
from .followed import ConfigFileError, FollowedFile


class Family(StrEnum):
    EVM = "evm"
    TRON = "tron"


class TxStatus(StrEnum):
    OK = "ok"              # included and successful
    FAILED = "failed"      # reverted, or gone from the chain (reorg / dropped)
    UNKNOWN = "unknown"    # not decided yet: still pending, or not solidified — ask again later


@dataclass(frozen=True)
class Token:
    symbol: str
    contract: str
    decimals: int


@dataclass(frozen=True)
class Chain:
    key: str
    family: Family
    name: str
    native_symbol: str
    native_decimals: int
    confirmations: int
    explorer: str                                  # block explorer base URL
    default_rpc_url: str = ""                      # from the chains file; HDPAY_RPC_URL_<KEY> overrides it
    chain_id: int | None = None                    # EVM only
    tokens: dict[str, Token] = field(default_factory=dict)
    standard: str | None = None                    # what its tokens are called; the family's name by default

    @property
    def rpc_url(self) -> str:
        return env_value(f"HDPAY_RPC_URL_{self.key.upper().replace('-', '_')}") or self.default_rpc_url

    @property
    def token_standard(self) -> str:
        """What the chain's tokens are called, shown next to a token symbol (USDT, BEP-20): the chains
        file's token_standard, or the family's usual name."""
        return self.standard or {Family.EVM: "ERC20", Family.TRON: "TRC20"}[self.family]

    def is_native(self, asset: str) -> bool:
        return asset == self.native_symbol

    def assets(self) -> list[str]:
        return [self.native_symbol, *self.tokens]

    def decimals(self, asset: str) -> int:
        return self.native_decimals if self.is_native(asset) else self.tokens[asset].decimals

    def explorer_tx(self, tx_hash: str) -> str:
        # Tronscan routes through a hash fragment; Etherscan-style explorers use plain paths
        return f"{self.explorer}/#/transaction/{tx_hash}" if self.family == Family.TRON else f"{self.explorer}/tx/{tx_hash}"

    def explorer_address(self, address: str) -> str:
        return f"{self.explorer}/#/address/{address}" if self.family == Family.TRON else f"{self.explorer}/address/{address}"


# ---- the file format, checked when it is loaded

KEY_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.]{1,16}$")
EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
TRON_ADDRESS = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")


class _TokenEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    contract: str
    decimals: int = Field(ge=0, le=36)

    @field_validator("contract", mode="before")
    @classmethod
    def _quoted(cls, value):
        if isinstance(value, int):
            raise ValueError('put the contract address in quotes: YAML reads an unquoted 0x... as a number')
        return value

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        if not SYMBOL_PATTERN.match(value):
            raise ValueError("symbol must be 1-16 capital letters, digits or dots, e.g. USDT")
        return value


class _Native(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    decimals: int = Field(ge=0, le=36)


class _ChainEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    family: Family
    name: str = Field(min_length=1)
    note: str | None = None                       # free text; a YAML comment does the same
    chain_id: int | None = Field(default=None, ge=1)
    native: _Native
    confirmations: int = Field(ge=1)
    explorer: str
    rpc_url: str = ""
    # what the chain's tokens are called (BEP-20 on BNB Smart Chain); ERC20 / TRC20 by family otherwise
    token_standard: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9-]{1,15}$")
    tokens: list[_TokenEntry] = []

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        if not KEY_PATTERN.match(value):
            raise ValueError("key must be lower-case letters and digits, joined by dashes, e.g. bsc-testnet")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "_ChainEntry":
        if self.family == Family.EVM and self.chain_id is None:
            raise ValueError("an evm chain needs chain_id, so a misconfigured RPC is caught")
        if self.family == Family.TRON and self.chain_id is not None:
            raise ValueError("a tron chain has no chain_id")
        pattern = EVM_ADDRESS if self.family == Family.EVM else TRON_ADDRESS
        symbols = [self.native.symbol]
        for token in self.tokens:
            if not pattern.match(token.contract):
                raise ValueError(f"{token.symbol}: {token.contract!r} is not a valid {self.family} contract address")
            symbols.append(token.symbol)
        duplicates = sorted({s for s in symbols if symbols.count(s) > 1})
        if duplicates:
            raise ValueError(f"asset symbols must be unique on a chain: {', '.join(duplicates)}")
        return self


class _ChainsFile(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    comment: str | None = Field(default=None, alias="$comment")
    chains: list[_ChainEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_keys(self) -> "_ChainsFile":
        keys = [c.key for c in self.chains]
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        if duplicates:
            raise ValueError(f"chain keys must be unique: {', '.join(duplicates)}")
        return self


class ChainsFileError(ConfigFileError):
    """The chains file is missing or not valid."""


def default_chains_file() -> Path:
    root = Path(__file__).resolve().parent.parent
    local = root / "chains.yaml"
    return local if local.exists() else root / "chains.example.yaml"


def load_chains(path: Path | str | None = None) -> dict[str, Chain]:
    path = Path(path) if path else default_chains_file()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ChainsFileError(f"chains file not found: {path} (set HDPAY_CHAINS_FILE)") from None
    except yaml.YAMLError as exc:
        raise ChainsFileError(f"{path} is not valid YAML: {' '.join(str(exc).split())}") from None
    if raw is None:
        raise ChainsFileError(f"{path} is empty")
    try:
        parsed = _ChainsFile.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ChainsFileError(f"{path} is not a valid chains file: {problems}") from None
    return {
        c.key: Chain(
            key=c.key,
            family=c.family,
            name=c.name,
            native_symbol=c.native.symbol,
            native_decimals=c.native.decimals,
            confirmations=c.confirmations,
            explorer=c.explorer.rstrip("/"),
            default_rpc_url=c.rpc_url,
            chain_id=c.chain_id,
            tokens={t.symbol: Token(t.symbol, t.contract, t.decimals) for t in c.tokens},
            standard=c.token_standard,
        )
        for c in parsed.chains
    }


log = logging.getLogger("hdpay.chains")


class ChainRegistry(FollowedFile[dict[str, Chain]], Mapping[str, Chain]):
    """The chains of the file, reloaded by `refresh()` when the file changes.

    A reload swaps the whole dict at once, so a reader iterating the old one is never disturbed,
    and every module holding `CHAINS` sees the new chains.
    """

    def __init__(self, path: Path | str | None = None):
        super().__init__(Path(path) if path else default_chains_file(), load_chains, "chains")

    def __getitem__(self, key: str) -> Chain:
        return self._value[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._value)

    def __len__(self) -> int:
        return len(self._value)


CHAINS = ChainRegistry(settings.chains_file or None)


def follow(worker, build):
    """The watcher or sweeper to use from now on: rebuilt when the file changed its chain.

    The new chain's tokens are checked against their contracts first; a chain that fails the
    check, or was removed from the file, leaves the running worker on its last settings.
    """
    if not CHAINS.refresh():
        return worker
    current = worker.chain
    changed = CHAINS.get(current.key)
    if changed is None:
        log.error("%s is no longer in the chains file; carrying on with its last settings", current.key)
        return worker
    if changed == current:
        return worker
    from .balances import verify_tokens

    try:
        notes = verify_tokens(changed)
        replacement = build(changed)
    except Exception as exc:
        message = re.sub(r"https?://\S+", "<rpc>", str(exc))
        log.error("%s changed in the chains file but was not applied, keeping its last settings: %s", current.key, message)
        return worker
    for note in notes:
        log.info("token %s", note)
    log.info("%s now runs with its new settings from the chains file", current.key)
    return replacement


def get_chain(key: str) -> Chain:
    try:
        return CHAINS[key]
    except KeyError:
        raise ValueError(f"unknown chain {key!r}, expected one of {', '.join(CHAINS)}") from None
