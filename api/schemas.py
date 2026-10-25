"""Request and response models."""

import ipaddress
import re
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .chains import CHAINS, EVM_ADDRESS, TRON_ADDRESS, Family
from .db import Deposit, Status, utcnow
from .keys import MAX_INDEX

# What the customer is told. Internally an "expired" address is still watched.
PUBLIC_STATUS = {
    Status.PENDING: "pending",
    Status.EXPIRED: "expired",
    Status.DETECTED: "confirming",
    Status.PARTIAL: "partial",
    Status.CONFIRMED: "paid",
    Status.SWEPT: "paid",
    Status.REVIEW: "contact_support",
}


def _chain_of(d: "Deposit"):
    from .chain_sync import chain_for

    return chain_for(d.chain, d.family)


def deposit_decimals(d: "Deposit") -> int:
    """The decimals of the deposit's coin: its own, else the chains' (the file's, or as the database
    remembers them). One never known reads as 0: amounts in base units, never a crash."""
    from .chain_sync import chain_for

    if d.has("decimals") and d.decimals is not None:
        return d.decimals
    chain = chain_for(d.chain, d.family)
    return chain.decimals(d.asset) if chain.lists(d.asset) else 0


def utc(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


# ---- admin sign-in

# deliberately loose: it only stops obvious typos
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _email(value: str) -> str:
    text = value.strip().lower()
    if not EMAIL_PATTERN.fullmatch(text):
        raise ValueError("email must look like name@example.com")
    return text


class Login(BaseModel):
    email: str
    password: str


class Refresh(BaseModel):
    refresh_token: str


class NewPassword(BaseModel):
    # bcrypt reads at most 72 bytes
    password: str = Field(min_length=8, max_length=72)


class ChangePassword(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=72)


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    projects: list[str] | None   # the projects an admin or viewer sees; None for all of them
    is_active: bool
    created_at: str
    last_login_at: str | None

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> "UserOut":
        return cls(
            id=doc["_id"], email=doc["email"], name=doc["name"], role=doc["role"],
            projects=None if doc["role"] == "owner" else doc.get("projects"), is_active=doc["is_active"],
            created_at=utc(doc["created_at"]), last_login_at=utc(doc.get("last_login_at")),
        )


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int              # seconds the access token is valid
    user: UserOut


class CreateUser(BaseModel):
    email: str
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=72)
    role: str = Field(pattern="^(owner|admin|viewer)$")
    # the projects this admin or viewer sees: null for all of them, or project ids (at least one)
    projects: list[str] | None = Field(default=None, min_length=1)

    _check_email = field_validator("email")(lambda cls, v: _email(v))


class UpdateUser(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = Field(default=None, pattern="^(owner|admin|viewer)$")
    is_active: bool | None = None
    # "all" for every project, or project ids (at least one); left out keeps the current ones
    projects: Literal["all"] | list[str] | None = None

    @field_validator("projects")
    @classmethod
    def _some_projects(cls, value: object) -> object:
        if isinstance(value, list) and not value:
            raise ValueError('give at least one project, or "all"')
        return value


# ---- projects

class Product(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    subname: str | None = Field(default=None, max_length=120)   # shown next to the name in product lists only
    # what the product costs, in USD, paid in USDT or USDC; None leaves the amount to the caller
    price: str | None = Field(default=None, pattern=r"^\d{1,12}(\.\d{1,2})?$", examples=["499", "99.50"])
    @field_validator("price", mode="before")
    @classmethod
    def _price(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        text = str(value).strip()
        if text.replace(".", "", 1).isdigit() and float(text) <= 0:
            raise ValueError("a price must be more than 0")
        return text

    @field_validator("value", "name", "subname", mode="before")
    @classmethod
    def _trimmed(cls, value: object) -> object:
        return " ".join(value.split()) or None if isinstance(value, str) else value


def _unique_products(products: list[Product]) -> list[Product]:
    values = [p.value for p in products]
    duplicates = sorted({v for v in values if values.count(v) > 1})
    if duplicates:
        raise ValueError(f"product values must be unique: {', '.join(duplicates)}")
    return products


def _treasury(treasury: dict[str, str | None]) -> dict[str, str | None]:
    patterns = {Family.EVM: EVM_ADDRESS, Family.TRON: TRON_ADDRESS}
    out = {}
    for family in Family:
        address = (treasury.get(family.value) or "").strip() or None
        if address and not patterns[family].match(address):
            raise ValueError(f"the {family} treasury is not a valid {family} address")
        out[family.value] = address
    unknown = set(treasury) - {f.value for f in Family}
    if unknown:
        raise ValueError(f"unknown chain family {', '.join(sorted(unknown))}")
    return out


def _allowed_ips(entries: list[str]) -> list[str]:
    out = []
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        try:
            out.append(str(ipaddress.ip_network(entry, strict=False)))
        except ValueError:
            raise ValueError(f"{entry!r} is not an IP address or a network like 203.0.113.0/24") from None
    return out


def _optional_text(value: str | None) -> str | None:
    """Trimmed; blank is none."""
    value = (value or "").strip()
    return value or None


def _website(value: str | None) -> str | None:
    value = _optional_text(value)
    if value is None:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc or any(c.isspace() for c in value):
        raise ValueError(f"{value!r} is not a web address like https://example.com")
    return value


class ProjectFields(BaseModel):
    """What a project is set up with; every field is optional so the same model serves updates."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)   # what the project is; for the admin
    website: str | None = Field(default=None, max_length=200)       # its site, http(s)
    products: list[Product] | None = None
    treasury: dict[str, str | None] | None = None
    chains: dict[str, list[str]] | None = None
    deposit_ttl_minutes: int | None = Field(default=None, ge=1, le=7 * 24 * 60)
    remainder_window_minutes: int | None = Field(default=None, ge=1, le=7 * 24 * 60)
    amount_tolerance_bps: int | None = Field(default=None, ge=0, le=10_000)
    allowed_ips: list[str] | None = None

    _check_description = field_validator("description")(lambda cls, v: _optional_text(v))
    _check_website = field_validator("website")(lambda cls, v: _website(v))
    _check_products = field_validator("products")(lambda cls, v: v if v is None else _unique_products(v))
    _check_treasury = field_validator("treasury")(lambda cls, v: v if v is None else _treasury(v))
    _check_ips = field_validator("allowed_ips")(lambda cls, v: v if v is None else _allowed_ips(v))

    @field_validator("chains")
    @classmethod
    def _chains(cls, chains: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        if chains is None:
            return None
        out = {}
        for key, assets in chains.items():
            chain = CHAINS.get(key)
            if chain is None:
                raise ValueError(f"unknown chain {key!r}, expected one of {', '.join(CHAINS)}")
            unknown = [a for a in assets if a not in chain.assets()]
            if unknown:
                raise ValueError(f"{chain.name} has no {', '.join(unknown)}")
            if assets:
                out[key] = [a for a in chain.assets() if a in assets]
        return out


class CreateProject(ProjectFields):
    name: str = Field(min_length=1, max_length=120)
    products: list[Product] = []
    treasury: dict[str, str | None] = {}
    chains: dict[str, list[str]] = {}
    allowed_ips: list[str] = []


class ProjectKeys(BaseModel):
    """The project's xpubs, from `python -m api.keys project <number>`; either family may wait."""

    evm: str | None = None
    tron: str | None = None

    @model_validator(mode="after")
    def _one_at_least(self) -> "ProjectKeys":
        if not (self.evm or self.tron):
            raise ValueError("paste at least one key")
        return self


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str | None
    website: str | None
    number: int
    status: str                       # awaiting_keys, active or disabled; locked in a user's list for one not theirs
    key_paths: dict[str, str]         # family -> m/{number}'/{coin}'
    xpubs: dict[str, str | None]
    treasury: dict[str, str | None]
    chains: dict[str, list[str]]
    products: list[Product]
    deposit_ttl_minutes: int
    remainder_window_minutes: int
    amount_tolerance_bps: int
    allowed_ips: list[str]
    events_acked: int
    created_at: str | None            # None, like everything but the name, on a locked project
    updated_at: str | None       # the last change: settings, keys or status


class ApiKeyOut(BaseModel):
    id: str
    kind: str                 # secret or publishable
    name: str
    prefix: str               # the first characters, to tell keys apart
    created_at: str
    last_used_at: str | None
    revoked_at: str | None

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> "ApiKeyOut":
        return cls(
            id=doc["_id"], kind=doc["kind"], name=doc["name"], prefix=doc["prefix"], created_at=utc(doc["created_at"]),
            last_used_at=utc(doc.get("last_used_at")), revoked_at=utc(doc.get("revoked_at")),
        )


class NewApiKey(BaseModel):
    kind: str = Field(pattern="^(secret|publishable)$")
    name: str = Field(min_length=1, max_length=120)


class CreatedApiKey(ApiKeyOut):
    key: str                  # shown once


# ---- chains and products

class AssetOut(BaseModel):
    symbol: str
    decimals: int
    contract: str | None        # None for the native coin
    standard: str | None        # ERC20 / TRC20 for a token, None for the native coin
    usd: bool = False           # a USD stablecoin (USDT, USDC): what priced products are paid in


class ChainOut(BaseModel):
    key: str
    name: str
    family: str
    confirmations: int
    assets: list[AssetOut]


class TokenChangeOut(BaseModel):
    contract: str
    decimals: int
    until: str                  # deposits opened before this keep this contract and decimals


class NetworkAssetOut(BaseModel):
    symbol: str
    native: bool
    contract: str | None        # None for the native coin
    decimals: int | None        # None while a new token is not confirmed by its contract yet
    # supported: in chains.yaml and confirmed; not supported: taken out of chains.yaml, kept for its
    # deposits; refused: the file's decimals contradict the contract; unchecked: new or changed in the
    # file, its contract not read yet (the RPC did not answer)
    status: str
    supported: bool
    supported_since: str | None
    supported_until: str | None
    verified_at: str | None
    problem: str | None         # why it is refused or unchecked
    deposits: int               # deposits ever opened in it on this network
    history: list[TokenChangeOut] = []


class NetworkOut(BaseModel):
    """A network chains.yaml lists or once listed, as the database keeps it."""
    key: str
    name: str
    family: str
    chain_id: int | None
    confirmations: int
    explorer: str
    token_standard: str
    supported: bool
    supported_since: str | None
    supported_until: str | None
    deposits: int
    assets: list[NetworkAssetOut]


class ProductOut(BaseModel):
    value: str
    name: str
    subname: str | None
    price: str | None           # USD, paid in USDT or USDC; None when the amount is up to the caller


# ---- deposits

class CreateDeposit(BaseModel):
    # a whole number, because it is a level of the derivation path
    account_id: str = Field(examples=["42"], description=f"Numeric account id in the project, 0 to {MAX_INDEX}")
    chain: str = Field(examples=["tron"])
    asset: str = Field(examples=["USDT"])
    # smallest unit (wei, sun, token base units); None accepts any amount
    expected_amount: int | None = Field(default=None, ge=1)
    # what the customer is paying for: a `value` from the project's products
    product: str | None = Field(default=None, max_length=120, examples=["100k"])
    # a discount for this deposit only, on a priced product: a percent off until a moment; paid in
    # full before then it holds, and anything still owed after it is owed at the full price
    discount_percent: int | None = Field(default=None, ge=1, le=99, examples=[20])
    discount_ends_at: datetime | None = Field(default=None, examples=["2026-10-31T18:00:00Z"])

    @field_validator("discount_ends_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        # stored naive UTC like every other time; a time without a zone is taken as UTC
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        if value is not None and value <= utcnow():
            raise ValueError("discount_ends_at must be in the future")
        return value

    @model_validator(mode="after")
    def _discount(self) -> "CreateDeposit":
        if (self.discount_percent is None) != (self.discount_ends_at is None):
            raise ValueError("a discount needs both discount_percent and discount_ends_at")
        return self

    @field_validator("product", mode="before")
    @classmethod
    def _trimmed_product(cls, value: object) -> str | None:
        text = " ".join(str(value).split()) if value is not None else ""
        return text or None

    @field_validator("account_id", mode="before")
    @classmethod
    def _numeric_account_id(cls, value: object) -> str:
        text = str(value).strip()
        if not text.isdigit() or int(text) > MAX_INDEX:
            raise ValueError(f"account_id must be a whole number from 0 to {MAX_INDEX}")
        return str(int(text))   # canonical: "042" and "42" are the same account


def _product_name(project: dict[str, Any] | None, value: str | None) -> str | None:
    from .projects import product_name

    return product_name(project, value)


class DiscountOut(BaseModel):
    """A deposit's discount, as it was when the deposit opened."""

    percent: int
    ends_at: str
    full_amount: str             # the full price, base units
    amount: str                  # the discounted price, base units
    status: str                  # active, kept (paid in time), lapsed (the full price is due), honored (by support)

    @classmethod
    def of(cls, d: Deposit) -> "DiscountOut | None":
        discount = d.discount
        if not discount:
            return None
        return cls(
            percent=discount["percent"], ends_at=utc(discount["ends_at"]), full_amount=discount["full_amount"],
            amount=discount["amount"], status=discount["status"],
        )


class DepositOut(BaseModel):
    """A deposit as the payer sees it: readable by anyone holding its id, so nothing internal."""

    id: str
    account_id: str
    product: str | None          # the stored value
    product_name: str | None     # what to show for it
    chain: str
    chain_name: str
    asset: str
    standard: str | None         # ERC20 / TRC20 for a token, None for the native coin
    decimals: int
    address: str
    address_url: str
    status: str
    # strings: 18-decimal amounts do not fit in a JavaScript number
    expected_amount: str | None
    received_amount: str
    tx_hash: str | None
    tx_url: str | None
    confirmations: int
    created_at: str
    expires_at: str
    expires_in_seconds: int
    discount: DiscountOut | None = None   # the discount the deposit was opened with, if any

    @classmethod
    def from_deposit(cls, d: Deposit, project: dict[str, Any] | None, **extra: Any) -> "DepositOut":
        chain = _chain_of(d)
        return cls(
            id=d.id,
            account_id=d.account_id,
            product=d.product,
            product_name=_product_name(project, d.product),
            chain=d.chain,
            chain_name=chain.name,
            asset=d.asset,
            standard=None if chain.is_native(d.asset) else chain.token_standard,
            decimals=deposit_decimals(d),
            address=d.address,
            address_url=chain.explorer_address(d.address),
            status=PUBLIC_STATUS[d.status],
            expected_amount=d.expected_amount,
            received_amount=d.received_amount,
            tx_hash=d.tx_hash,
            tx_url=chain.explorer_tx(d.tx_hash) if d.tx_hash else None,
            confirmations=chain.confirmations,
            created_at=utc(d.created_at),
            expires_at=utc(d.expires_at),
            expires_in_seconds=max(int((d.expires_at - utcnow()).total_seconds()), 0),
            discount=DiscountOut.of(d),
            **extra,
        )


class ProjectDepositOut(DepositOut):
    """A deposit as its project's backend sees it, in API replies and in every event."""

    internal_status: Status
    difference: str | None       # over, under or exact against the expected amount
    late: bool                   # paid after the payment window
    confirmed_at: str | None
    swept_at: str | None
    sweep_tx_hash: str | None

    @classmethod
    def from_deposit(cls, d: Deposit, project: dict[str, Any] | None, **extra: Any) -> "ProjectDepositOut":
        return super().from_deposit(
            d, project,
            internal_status=d.status, difference=d.difference, late=d.late, confirmed_at=utc(d.confirmed_at),
            swept_at=utc(d.swept_at), sweep_tx_hash=d.sweep_tx_hash,
            **extra,
        )


class PaymentOut(BaseModel):
    """One payment counted on a deposit."""

    tx_hash: str | None          # None when it was found from the address's balance (sent through a contract)
    tx_url: str | None
    block_number: int | None
    amount: str                  # base units
    paid_at: str | None          # the block's time
    source: str                  # scan, check (a support check) or balance
    confirmed: bool


class CheckFound(BaseModel):
    tx_hash: str | None
    amount: str
    paid_at: str | None


class CheckOut(BaseModel):
    """A support check of the deposit's address, run by the chain's watcher."""

    status: str                  # waiting (for the watcher) or done
    requested_at: str
    requested_by: str
    checked_at: str | None
    from_block: int | None
    to_block: int | None
    next_block: int | None = None   # while it runs: the next block it reads
    exact: bool | None           # False: the chain could not tell block times, so recent blocks only
    found: list[CheckFound]      # every payment on the chain in that range
    counted: str | None          # newly counted by this check, base units
    from_balance: str | None     # of which, found only in the balance
    error: str | None


class ChainView(BaseModel):
    """The deposit's address as the chain sees it now."""

    balance: str | None          # base units; None when the RPC could not be read
    error: str | None
    watcher_seen_at: str | None  # the chain's watcher last ran; None when it never did


class AdminDepositOut(ProjectDepositOut):
    """Everything about a deposit, for support and operations."""

    project_id: str
    project_name: str | None
    family: str
    path: str                    # full derivation path; admin only
    derivation_date: str
    index: int
    block_number: int | None
    fee_tx_hash: str | None
    fee_tx_url: str | None
    sweep_tx_url: str | None
    sweep_hold: str | None
    payments: list[PaymentOut] = []
    check: CheckOut | None = None
    # False: accepted when this deposit opened, not supported now (taken out of chains.yaml since)
    asset_supported: bool = True

    @classmethod
    def from_deposit(cls, d: Deposit, project: dict[str, Any] | None, **extra: Any) -> "AdminDepositOut":
        chain = _chain_of(d)
        check = d.check
        if check:
            check = CheckOut(
                status=check["status"], requested_at=utc(check["requested_at"]), requested_by=check["requested_by"],
                checked_at=utc(check.get("checked_at")), from_block=check.get("from_block"),
                to_block=check.get("to_block"), next_block=check.get("next_block"), exact=check.get("exact"),
                found=[CheckFound(tx_hash=f["tx_hash"], amount=f["amount"], paid_at=utc(f.get("paid_at")))
                       for f in check.get("found") or []],
                counted=check.get("counted"), from_balance=check.get("from_balance"), error=check.get("error"),
            )
        return super().from_deposit(
            d, project,
            project_id=d.project_id,
            project_name=(project or {}).get("name"),
            family=d.family,
            path=d.path,
            derivation_date=d.derivation_date,
            index=d.index,
            block_number=d.block_number,
            fee_tx_hash=d.fee_tx_hash,
            fee_tx_url=chain.explorer_tx(d.fee_tx_hash) if d.fee_tx_hash else None,
            sweep_tx_url=chain.explorer_tx(d.sweep_tx_hash) if d.sweep_tx_hash else None,
            sweep_hold=d.sweep_hold,
            payments=[
                PaymentOut(
                    tx_hash=p.get("tx_hash"), tx_url=chain.explorer_tx(p["tx_hash"]) if p.get("tx_hash") else None,
                    block_number=p.get("block_number"), amount=p["amount"], paid_at=utc(p.get("paid_at")),
                    source=p.get("source", "scan"), confirmed=bool(p.get("confirmed")),
                )
                for p in d.payments
            ],
            check=check,
            asset_supported=d.chain in CHAINS and CHAINS[d.chain].lists(d.asset),
            **extra,
        )


class ReopenDeposit(BaseModel):
    """Support's terms for the rest of an underpayment; all default to the usual."""

    # what the customer still has to send, in base units; the expected amount becomes received + this
    remaining_amount: str | None = Field(default=None, pattern=r"^[1-9][0-9]{0,77}$")
    # how long the address waits for it, from now (up to a week)
    minutes: int | None = Field(default=None, ge=1, le=7 * 24 * 60)
    # change what the deposit is for: a `value` from the project's products; None keeps the current one
    product: str | None = Field(default=None, max_length=120)
    # after a discount ended unpaid, the rest is the full price less what came; true keeps the discount
    keep_discount: bool = False


class AdminDepositList(BaseModel):
    total: int
    skip: int
    limit: int
    items: list[AdminDepositOut]


class AdminDefaults(BaseModel):
    deposit_ttl_minutes: int
    remainder_window_minutes: int
    amount_tolerance_bps: int


# ---- events

class EventOut(BaseModel):
    id: str
    seq: int
    type: str
    created_at: str
    deposit: dict[str, Any]


class EventList(BaseModel):
    events: list[EventOut]
    last_seq: int             # the sequence to ask after next time


class Acknowledge(BaseModel):
    seq: int = Field(ge=1)


# ---- operations

class AssetTotal(BaseModel):
    chain: str
    chain_name: str
    asset: str
    decimals: int
    status: Status   # confirmed (credited, not swept yet) or swept
    count: int
    amount: str      # smallest unit, credited deposits only


class WatcherState(BaseModel):
    chain: str
    chain_name: str
    last_block: int | None
    head: int | None
    heartbeat_at: str | None


class StatsOut(BaseModel):
    total: int
    by_status: dict[str, int]
    totals: list[AssetTotal]
    watchers: list[WatcherState]
    projects: int


class FunderBalance(BaseModel):
    chain: str
    chain_name: str
    symbol: str
    decimals: int
    balance: str | None        # smallest unit; None when the RPC could not be read
    error: str | None
    address_url: str


class FunderBalancesOut(BaseModel):
    family: str
    path: str                  # m/{project}'/{coin}'/{year}: the funder changes every UTC year
    address: str
    balances: list[FunderBalance]


class TreasuryBalance(BaseModel):
    chain: str
    chain_name: str
    asset: str
    decimals: int
    balance: str | None        # smallest unit; None when the RPC could not be read
    error: str | None
    address_url: str


class TreasuryOut(BaseModel):
    family: str
    address: str | None        # None until the project sets one
    balances: list[TreasuryBalance]


class SweeperState(BaseModel):
    chain: str
    chain_name: str
    family: str
    heartbeat_at: str | None


class SweeperOut(BaseModel):
    """Everything the admin's Sweeper page shows."""

    counts: dict[str, int]     # waiting, topping_up, sweeping, held, swept
    sweepers: list[SweeperState]
    queue: list[AdminDepositOut]         # a page of the credited, not swept yet: held first, then oldest first
    queue_page: int = 1
    queue_size: int = 20
    recent: list[AdminDepositOut]        # a page of the sweeps, newest first; counts["swept"] is the total
    recent_page: int = 1
    recent_size: int = 20
