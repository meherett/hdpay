"""
Projects: the apps that take payments through HDPay.

A project document:

    _id          a MongoDB ObjectId, as its 24 hex characters ("65f1c2a9e4b0d1a2b3c4d5e6")
    name         shown in the admin
    description  what the project is (optional)
    website      its site, http(s) (optional)
    number       random hardened derivation index, the first level of every path: m/{number}'/...
    status       awaiting_keys -> active <-> disabled
    xpubs        {"evm": xpub | None, "tron": xpub | None}, the project's m/{number}'/{coin}' nodes
    treasury     {"evm": address | None, "tron": address | None}, where its deposits are swept
    chains       {"sepolia": ["ETH", "USDC"], ...}, the networks and assets it accepts
    products     [{"value": "100k", "name": "$100,000", "subname": "(Most popular)", "price": "499"}, ...]
                 price (optional) is in USD and paid in a USD stablecoin; it sets the deposit's amount
    deposit_ttl_minutes, remainder_window_minutes, amount_tolerance_bps
    allowed_ips  API keys only work from these addresses; empty allows any
    events_acked the last event sequence the project acknowledged
"""

import secrets
from decimal import Decimal
from typing import Any

from bson import ObjectId
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from .chains import CHAINS, Chain, Family
from .config import settings
from .db import utcnow
from .keys import MAX_INDEX


# Coins a product's USD price is paid in, one coin to the dollar. Native coins (ETH, BNB, TRX) have no
# fixed price, so a priced product is paid in one of these.
USD_STABLECOINS = ("USDT", "USDC")


def price_amount(price: str, decimals: int) -> int:
    """A USD price in an asset's base units, exactly: 499 at 6 decimals is 499000000."""
    return int(Decimal(price).scaleb(decimals))


class ProjectStatus:
    AWAITING_KEYS = "awaiting_keys"
    ACTIVE = "active"
    DISABLED = "disabled"


def new_project(db: Database, fields: dict[str, Any], created_by: str | None) -> dict[str, Any]:
    """Insert a project under a fresh random number; its keys come next, from the master key's host."""
    now = utcnow()
    for _ in range(10):
        doc = {
            "_id": str(ObjectId()),
            "number": secrets.randbelow(MAX_INDEX) + 1,
            "status": ProjectStatus.AWAITING_KEYS,
            "xpubs": {family.value: None for family in Family},
            "events_acked": 0,
            "created_at": now,
            "updated_at": now,
            "created_by": created_by,
            **fields,
        }
        try:
            db.projects.insert_one(doc)
            return doc
        except DuplicateKeyError:
            continue   # the number is taken: draw another
    raise RuntimeError("could not find a free project number")


def migrate_project_ids(db: Database) -> list[tuple[str, str]]:
    """Give every project with an old "prj_..." id a MongoDB ObjectId, and move everything that names it:
    its deposits, API keys, events, users' project lists, and its counters (address indexes and event
    sequence, so no address is ever handed out twice). Returns (old, new) pairs. Run it while the
    watchers and sweepers are stopped."""
    moved = []
    for project in list(db.projects.find({"_id": {"$regex": "^prj_"}})):
        old, new = project["_id"], str(ObjectId())
        # the number is unique, so the old document goes first; the copy in hand holds everything
        db.projects.delete_one({"_id": old})
        db.projects.insert_one(project | {"_id": new})
        for collection in ("deposits", "api_keys", "events"):
            db[collection].update_many({"project_id": old}, {"$set": {"project_id": new}})
        for user in db.users.find({"projects": old}):
            db.users.update_one({"_id": user["_id"]}, {"$set": {"projects": [new if p == old else p for p in user["projects"]]}})
        for counter in list(db.counters.find({"_id": {"$regex": f"^(address_index|event_seq):{old}(:|$)"}})):
            db.counters.insert_one(counter | {"_id": counter["_id"].replace(old, new, 1)})
            db.counters.delete_one({"_id": counter["_id"]})
        moved.append((old, new))
    return moved


def get_project(db: Database, project_id: str) -> dict[str, Any] | None:
    return db.projects.find_one({"_id": project_id})


def projects_by_id(db: Database) -> dict[str, dict[str, Any]]:
    return {p["_id"]: p for p in db.projects.find()}


def setting(project: dict[str, Any], name: str) -> int:
    """A project's own value for deposit_ttl_minutes, remainder_window_minutes or amount_tolerance_bps."""
    value = project.get(name)
    return value if value is not None else getattr(settings, name)


def accepted(project: dict[str, Any]) -> list[tuple[Chain, list[str]]]:
    """The networks and assets a project takes now: listed by it, still in the chains file, and with
    the project's key for the chain family in place."""
    out = []
    for key, assets in (project.get("chains") or {}).items():
        chain = CHAINS.get(key)
        if chain is None or not (project.get("xpubs") or {}).get(chain.family):
            continue
        usable = [a for a in chain.assets() if a in assets]
        if usable:
            out.append((chain, usable))
    return out


def product_name(project: dict[str, Any] | None, value: str | None) -> str | None:
    """The name to show for a stored product; a value the project no longer lists is shown as it is."""
    if value is None:
        return None
    for product in (project or {}).get("products") or []:
        if product["value"] == value:
            return product["name"]
    return value
