"""
MongoDB: the connection, the collections' indexes and the deposit record.

Collections
    users            admin sign-in accounts
    sessions         refresh tokens (hashed), removed when they expire
    login_attempts   wrong-password counters, removed when their window ends
    projects         the apps that take payments through HDPay, with their products and settings
    api_keys         project API keys (hashed)
    deposits         one per issued address
    events           what happened to each project's deposits, for its WebSocket and GET /v1/events
    counters         address indexes, event sequences, watcher cursors and heartbeats

Amounts are base-unit integers stored as strings: 10 ETH in wei does not fit a 64-bit integer.
Times are naive UTC.
"""

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.database import Database

from .config import settings

_client: MongoClient | None = None


def utcnow() -> datetime:
    # naive UTC, millisecond precision: what MongoDB stores, so a value reads back equal
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=15_000)
    return _client


def get_db() -> Database:
    return client()[settings.mongodb_db]


def use_client(replacement: MongoClient | None) -> None:
    """Swap the connection, e.g. for an in-memory one in tests."""
    global _client
    _client = replacement


def create_indexes(db: Database | None = None) -> None:
    """Idempotent; run at every start of the API, the watchers and the sweepers."""
    db = db if db is not None else get_db()
    db.users.create_index("email", unique=True)
    db.sessions.create_index("token_hash", unique=True)
    db.sessions.create_index("user_id")
    db.sessions.create_index("expires_at", expireAfterSeconds=0)
    db.login_attempts.create_index("expires_at", expireAfterSeconds=0)
    db.projects.create_index("number", unique=True)
    db.api_keys.create_index("key_hash", unique=True)
    db.api_keys.create_index("project_id")
    db.deposits.create_index("address", unique=True)
    db.deposits.create_index([("project_id", ASCENDING), ("created_at", DESCENDING)])
    db.deposits.create_index([("chain", ASCENDING), ("status", ASCENDING)])
    db.deposits.create_index([("project_id", ASCENDING), ("account_id", ASCENDING), ("status", ASCENDING)])
    db.deposits.create_index("created_at")
    db.events.create_index([("project_id", ASCENDING), ("seq", ASCENDING)], unique=True)
    db.events.create_index("created_at", expireAfterSeconds=settings.event_retention_days * 86_400)
    upgrade(db)


def upgrade(db: Database) -> None:
    """Stored values an older version wrote, renamed since. Idempotent, like the indexes."""
    # the status "needs_review" is now "review", in the deposits and in the events' copies of them
    db.deposits.update_many({"status": "needs_review"}, {"$set": {"status": Status.REVIEW.value}})
    db.events.update_many({"deposit.internal_status": "needs_review"}, {"$set": {"deposit.internal_status": Status.REVIEW.value}})


# ---- counters: address indexes, event sequences, watcher cursors and heartbeats

def next_value(db: Database, key: str) -> int:
    """Atomically take the next value of a counter that starts at 0."""
    doc = db.counters.find_one_and_update(
        {"_id": key}, {"$inc": {"value": 1}}, upsert=True, return_document=ReturnDocument.AFTER
    )
    return doc["value"] - 1


def get_counter(db: Database, key: str) -> int | None:
    doc = db.counters.find_one({"_id": key})
    return doc["value"] if doc else None


def set_counter(db: Database, key: str, value: int) -> None:
    db.counters.update_one({"_id": key}, {"$set": {"value": value}}, upsert=True)


# ---- deposits

class Status(str, enum.Enum):
    PENDING = "pending"            # address issued, inside its payment window
    EXPIRED = "expired"            # window passed, nothing received: STILL WATCHED
    DETECTED = "detected"          # payment seen, waiting for confirmations
    PARTIAL = "partial"            # underpaid and confirmed; the rest may still arrive until expires_at
    CONFIRMED = "confirmed"        # credited to the account
    REVIEW = "review"              # the rest never came: not watched any more, support decides
    SWEPT = "swept"                # funds moved to the project's treasury


def difference_of(expected: str | None, received: str) -> str | None:
    """over / under / exact against the expected amount; None while nothing arrived or any amount goes."""
    if expected is None or int(received) == 0:
        return None
    gap = int(received) - int(expected)
    return "over" if gap > 0 else "under" if gap < 0 else "exact"


class Deposit:
    """A deposit document with attribute access. `save` writes only the fields that changed, so the
    watcher, the sweeper and the admin can each update their part of the same deposit."""

    def __init__(self, doc: dict[str, Any]):
        object.__setattr__(self, "_doc", doc)
        object.__setattr__(self, "_changed", set())

    @classmethod
    def new(cls, **fields: Any) -> "Deposit":
        now = utcnow()
        doc = {
            "_id": str(uuid.uuid4()),
            "received_amount": "0",
            "confirmed_amount": None,
            "status": Status.PENDING.value,
            "late": False,
            "tx_hash": None,
            "block_number": None,
            "fee_tx_hash": None,
            "sweep_tx_hash": None,
            "sweep_hold": None,
            "swept_at": None,
            "confirmed_at": None,
            "created_at": now,
            "updated_at": now,
            **fields,
        }
        doc["difference"] = difference_of(doc.get("expected_amount"), doc["received_amount"])
        if isinstance(doc["status"], Status):
            doc["status"] = doc["status"].value
        return cls(doc)

    @property
    def id(self) -> str:
        return self._doc["_id"]

    @property
    def discount(self) -> dict[str, Any] | None:
        """The product discount the deposit opened with (api.discounts); None for most deposits.
        Replace it whole (deposit.discount = {...}) so the change is saved."""
        return self._doc.get("discount")

    @property
    def payments(self) -> list[dict[str, Any]]:
        """Every payment counted, oldest first: {tx_hash, block_number, amount, paid_at, source,
        confirmed}. A copy: assign a new list (deposit.payments = [...]) so the change is saved."""
        return list(self._doc.get("payments") or [])

    @property
    def check(self) -> dict[str, Any] | None:
        """A support check of the address, asked for in the admin and run by the chain's watcher."""
        return self._doc.get("check")

    @property
    def counted_to(self) -> int | None:
        """Every transfer to the address up to this block is already in received_amount: a balance was
        read there, or, for a deposit from before payments were listed, it is its last counted block."""
        marks = [self._doc.get("balance_block"), self._doc.get("legacy_counted_to")]
        if "payments" not in self._doc and int(self._doc.get("received_amount") or 0) > 0:
            marks.append(self._doc.get("block_number"))
        marks = [m for m in marks if m is not None]
        return max(marks) if marks else None

    def has(self, name: str) -> bool:
        return name in self._doc

    def __getattr__(self, name: str) -> Any:
        try:
            value = self._doc[name]
        except KeyError:
            raise AttributeError(name) from None
        return Status(value) if name == "status" else value

    def __setattr__(self, name: str, value: Any) -> None:
        if isinstance(value, Status):
            value = value.value
        self._doc[name] = value
        self._changed.add(name)

    def document(self) -> dict[str, Any]:
        return dict(self._doc)

    def insert(self, db: Database) -> None:
        db.deposits.insert_one(self._doc)
        self._changed.clear()

    def save(self, db: Database) -> None:
        if not self._changed:
            return
        if self._changed & {"received_amount", "expected_amount"}:
            self._doc["difference"] = difference_of(self._doc.get("expected_amount"), self._doc["received_amount"])
            self._changed.add("difference")
        self._doc["updated_at"] = utcnow()
        changes = {name: self._doc[name] for name in self._changed | {"updated_at"}}
        db.deposits.update_one({"_id": self.id}, {"$set": changes})
        self._changed.clear()


def find_deposit(db: Database, deposit_id: str) -> Deposit | None:
    doc = db.deposits.find_one({"_id": deposit_id})
    return Deposit(doc) if doc else None


def find_deposits(db: Database, query: dict[str, Any], **kwargs: Any) -> list[Deposit]:
    return [Deposit(doc) for doc in db.deposits.find(query, **kwargs)]
