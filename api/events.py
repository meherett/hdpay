"""
Project events: everything that happens to a project's deposits, in order.

Each event is stored with a per-project sequence number, so the project's backend never misses one:
it reads them over the WebSocket (GET /v1/events/stream) or with GET /v1/events?after=<seq>, and
acknowledges what it handled. After a disconnect it resumes after the last acknowledged sequence.
Delivery is at least once: a project ignores an event id it has already handled.

    deposit.created     a new address was issued
    deposit.detected    a payment was seen, waiting for confirmations
    deposit.partial     confirmed, but less than expected: the rest may still come to the same address
    deposit.reopened    support gave a deposit in review a new window for the rest
    deposit.confirmed   paid: credit the account
    deposit.review      the rest never came; the address is closed and the customer contacts support
    deposit.expired     the payment window ended with nothing received (a late payment is still counted)
    deposit.swept       the funds reached the project's treasury
    deposit.discount_expired  a product discount ended before the deposit was paid: the full price is due

Events are kept for HDPAY_EVENT_RETENTION_DAYS.
"""

from typing import Any

from pymongo import ASCENDING
from pymongo.database import Database

from .db import Deposit, Status, next_value, utcnow

EVENT_FOR_STATUS = {
    Status.DETECTED: "deposit.detected",
    Status.PARTIAL: "deposit.partial",
    Status.CONFIRMED: "deposit.confirmed",
    Status.NEEDS_REVIEW: "deposit.review",
    Status.EXPIRED: "deposit.expired",
    Status.SWEPT: "deposit.swept",
}
TYPES = ("deposit.created", "deposit.reopened", "deposit.discount_expired", *EVENT_FOR_STATUS.values())


def emit(db: Database, deposit: Deposit, event_type: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Record an event for the deposit's project; `snapshot` is the deposit as the project sees it."""
    if event_type not in TYPES:
        raise ValueError(f"unknown event {event_type}")
    project_id = deposit.project_id
    seq = next_value(db, f"event_seq:{project_id}") + 1
    doc = {
        "_id": f"evt_{project_id}_{seq}",
        "project_id": project_id,
        "seq": seq,
        "type": event_type,
        "created_at": utcnow(),
        "deposit": snapshot,
    }
    db.events.insert_one(doc)
    return doc


def after(db: Database, project_id: str, seq: int, limit: int = 100) -> list[dict[str, Any]]:
    """The project's events with a sequence above `seq`, oldest first."""
    cursor = db.events.find({"project_id": project_id, "seq": {"$gt": seq}}).sort("seq", ASCENDING).limit(limit)
    return list(cursor)


def acknowledge(db: Database, project_id: str, seq: int) -> None:
    """The project handled everything up to `seq`; it never moves back."""
    db.projects.update_one({"_id": project_id}, {"$max": {"events_acked": seq}})


def public(event: dict[str, Any]) -> dict[str, Any]:
    """An event as sent to the project."""
    return {
        "id": event["_id"],
        "seq": event["seq"],
        "type": event["type"],
        "created_at": event["created_at"].isoformat() + "Z",
        "deposit": event["deposit"],
    }
