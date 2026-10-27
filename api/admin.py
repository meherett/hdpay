"""Support and operations endpoints, for the hdpay admin app. Every route needs a signed-in user;
changes need the admin role."""

import re
from collections import Counter as Tally
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from . import apikeys, discounts, events
from .auth import AdminUser, CurrentUser, OwnerUser, can_see, new_user, project_scope, set_password
from .balances import asset_balances, balance, native_balances, rpc_problem
from .chain_sync import chain_for, load_removed, same_contract
from .chains import CHAINS, Family
from .config import settings
from .db import Deposit, Status, get_counter, get_db, utcnow
from .deposits import get_deposit_or_404, notify, notify_status, reopen
from .keys import check_project_xpub, coin_path, funder_address, funder_path
from .projects import (
    USD_STABLECOINS,
    ProjectStatus,
    accepted,
    get_project,
    new_project,
    price_amount,
    projects_by_id,
    setting,
)
from .schemas import (
    deposit_decimals,
    ChainView,
    AdminDefaults,
    AdminDepositList,
    AdminDepositOut,
    ApiKeyOut,
    AssetOut,
    AssetTotal,
    ChainOut,
    NetworkAssetOut,
    NetworkOut,
    TokenChangeOut,
    CreatedApiKey,
    CreateProject,
    CreateUser,
    EventOut,
    FunderBalance,
    FunderBalancesOut,
    NewApiKey,
    NewPassword,
    ProjectFields,
    ProjectKeys,
    ProjectOut,
    ReopenDeposit,
    StatsOut,
    SweeperOut,
    SweeperState,
    TreasuryBalance,
    TreasuryOut,
    UpdateUser,
    UserOut,
    WatcherState,
    utc,
)

# every route takes CurrentUser (read) or AdminUser (change)
router = APIRouter(prefix="/admin", tags=["admin"])
DB = Annotated[Database, Depends(get_db)]


def _iso_timestamp(seconds: int | None) -> str | None:
    if not seconds:
        return None
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def _project_or_404(db: Database, project_id: str, user: dict[str, Any]) -> dict[str, Any]:
    """A project the user may see; one outside their projects is not found, as if it did not exist."""
    project = get_project(db, project_id)
    if project is None or not can_see(user, project_id):
        raise HTTPException(404, "project not found")
    return project


def _deposit_or_404(db: Database, deposit_id: str, user: dict[str, Any]) -> Deposit:
    deposit = get_deposit_or_404(db, deposit_id)
    if not can_see(user, deposit.project_id):
        raise HTTPException(404, "deposit not found")
    return deposit


def _in_scope(user: dict[str, Any]) -> dict[str, Any]:
    """A query condition for the user's projects: nothing for all of them."""
    scope = project_scope(user)
    return {} if scope is None else {"project_id": {"$in": scope}}


def _admin_deposit(d: Deposit, projects: dict[str, dict[str, Any]]) -> AdminDepositOut:
    return AdminDepositOut.from_deposit(d, projects.get(d.project_id))


def _chains_in_use(db: Database) -> list:
    """Chains at least one project accepts: those have a watcher and a sweeper."""
    keys = set()
    for project in db.projects.find({"status": ProjectStatus.ACTIVE}):
        keys.update(c.key for c, _ in accepted(project))
    return [c for c in CHAINS.values() if c.key in keys]


# ---- overview

@router.get("/stats", response_model=StatsOut)
def stats(user: CurrentUser, db: DB, project_id: str | None = None):
    if project_id:
        _project_or_404(db, project_id, user)
    match = {"project_id": project_id} if project_id else _in_scope(user)
    by_status = {s.value: 0 for s in Status}
    for row in db.deposits.aggregate([{"$match": match}, {"$group": {"_id": "$status", "count": {"$sum": 1}}}]):
        by_status[row["_id"]] = row["count"]

    # amounts are strings, so they are summed here: one row per chain, asset and credited status
    credited_statuses = (Status.CONFIRMED.value, Status.SWEPT.value)
    counts, amounts = Tally(), Tally()
    projection = {"chain": 1, "asset": 1, "status": 1, "received_amount": 1}
    for d in db.deposits.find({**match, "status": {"$in": list(credited_statuses)}}, projection):
        key = (d["chain"], d["asset"], d["status"])
        counts[key] += 1
        amounts[key] += int(d["received_amount"])
    totals = [
        AssetTotal(
            chain=chain, chain_name=chain_for(chain).name, asset=asset,
            decimals=chain_for(chain).decimals(asset) if chain_for(chain).lists(asset) else 0,
            status=status, count=counts[(chain, asset, status)], amount=str(amounts[(chain, asset, status)]),
        )
        for chain, asset, status in sorted(counts, key=lambda k: (k[0], k[1], credited_statuses.index(k[2])))
    ]

    watchers = [
        WatcherState(
            chain=c.key,
            chain_name=c.name,
            last_block=get_counter(db, f"last_block:{c.key}"),
            head=get_counter(db, f"head:{c.key}"),
            heartbeat_at=_iso_timestamp(get_counter(db, f"heartbeat:{c.key}")),
        )
        for c in _chains_in_use(db)
    ]
    return StatsOut(
        total=sum(by_status.values()), by_status=by_status, totals=totals, watchers=watchers,
        projects=db.projects.count_documents({} if project_scope(user) is None else {"_id": {"$in": project_scope(user)}}),
    )


@router.get("/defaults", response_model=AdminDefaults)
def defaults(user: CurrentUser):
    """Server settings the admin app offers as starting values."""
    return AdminDefaults(
        deposit_ttl_minutes=settings.deposit_ttl_minutes,
        remainder_window_minutes=settings.remainder_window_minutes,
        amount_tolerance_bps=settings.amount_tolerance_bps,
    )


@router.get("/chains", response_model=list[ChainOut])
def all_chains(user: CurrentUser):
    """Every network and asset in the chains file, for the admin's project form and filters."""
    return [
        ChainOut(
            key=c.key, name=c.name, family=c.family, confirmations=c.confirmations,
            assets=[
                AssetOut(
                    symbol=a, decimals=c.decimals(a),
                    contract=None if c.is_native(a) else c.tokens[a].contract,
                    standard=None if c.is_native(a) else c.token_standard,
                    usd=a in USD_STABLECOINS,
                )
                for a in c.assets()
            ],
        )
        for c in CHAINS.values()
    ]


@router.get("/networks", response_model=list[NetworkOut])
def networks(user: CurrentUser, db: DB):
    """Every network and coin chains.yaml lists or once listed: which are supported now, which were
    accepted before and are not any more, and how many deposits each had."""
    used: dict[tuple[str, str], int] = {}
    for row in db.deposits.aggregate([{"$match": _in_scope(user)},
                                      {"$group": {"_id": {"chain": "$chain", "asset": "$asset"}, "n": {"$sum": 1}}}]):
        used[(row["_id"]["chain"], row["_id"]["asset"])] = row["n"]
    stored = {doc["_id"]: doc for doc in db.chains.find()}
    load_removed(db, CHAINS)   # the networks taken out of chains.yaml, as the database keeps them
    out = []
    for key in [*CHAINS, *sorted(k for k in stored if k not in CHAINS)]:
        doc, file = stored.get(key) or {}, CHAINS.get(key)
        chain = chain_for(key, doc.get("family"))
        supported = file is not None
        since = utc(doc.get("supported_since") or doc.get("added_at"))
        tokens = doc.get("tokens") or {}
        assets = [NetworkAssetOut(
            symbol=chain.native_symbol, native=True, contract=None, decimals=chain.native_decimals,
            status="supported" if supported else "not supported", supported=supported,
            supported_since=since, supported_until=None if supported else utc(doc.get("supported_until")),
            verified_at=None, problem=None, deposits=used.get((key, chain.native_symbol), 0),
        )]
        symbols = [*(file.tokens if file else []), *sorted(s for s in tokens if not (file and s in file.tokens))]
        for symbol in symbols:
            t, listed = tokens.get(symbol) or {}, file.tokens.get(symbol) if file else None
            refused = t.get("refused")
            if listed is None:
                status, problem = "not supported", None
            elif refused:
                status = "refused"
                problem = (f"chains.yaml says {refused['decimals']} decimals for {refused['contract']}, "
                           f"the contract has {refused['on_chain']}: no new deposits until it is fixed")
            elif t.get("decimals") != listed.decimals or not same_contract(chain, t.get("contract"), listed.contract):
                status, problem = "unchecked", "new or changed in chains.yaml; its contract is read when the API starts"
            else:
                status, problem = "supported", None
            assets.append(NetworkAssetOut(
                symbol=symbol, native=False,
                contract=t.get("contract") or (listed and listed.contract),
                decimals=t["decimals"] if t.get("decimals") is not None else (listed and listed.decimals),
                status=status, supported=listed is not None,
                supported_since=utc(t.get("supported_since")) if t else None,
                supported_until=None if listed is not None else utc(t.get("supported_until")),
                verified_at=utc(t.get("verified_at")), problem=problem, deposits=used.get((key, symbol), 0),
                history=[TokenChangeOut(contract=h["contract"], decimals=h["decimals"], until=utc(h["until"]))
                         for h in t.get("history") or []],
            ))
        out.append(NetworkOut(
            key=key, name=chain.name, family=chain.family, chain_id=chain.chain_id,
            confirmations=chain.confirmations, explorer=chain.explorer, token_standard=chain.token_standard,
            supported=supported, supported_since=since,
            supported_until=None if supported else utc(doc.get("supported_until")),
            deposits=sum(n for (c, _), n in used.items() if c == key), assets=assets,
        ))
    return out


# ---- users

@router.get("/users", response_model=list[UserOut])
def list_users(owner: OwnerUser, db: DB):
    return [UserOut.from_document(u) for u in db.users.find().sort("created_at", ASCENDING)]


def _known_projects(db: Database, projects: list[str] | None) -> None:
    if projects is None:
        return
    found = {p["_id"] for p in db.projects.find({"_id": {"$in": projects}}, {"_id": 1})}
    missing = [p for p in projects if p not in found]
    if missing:
        raise HTTPException(422, f"no such project: {', '.join(missing)}")


def _last_owner(db: Database, user_id: str) -> bool:
    """Whether this user is the only active owner left."""
    others = db.users.count_documents({"role": "owner", "is_active": True, "_id": {"$ne": user_id}})
    target = db.users.find_one({"_id": user_id}) or {}
    return target.get("role") == "owner" and others == 0


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: CreateUser, owner: OwnerUser, db: DB):
    _known_projects(db, body.projects)
    try:
        return UserOut.from_document(new_user(db, body.email, body.name, body.password, body.role, body.projects))
    except DuplicateKeyError:
        raise HTTPException(409, "a user with this email already exists") from None


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UpdateUser, owner: OwnerUser, db: DB):
    changes = body.model_dump(exclude_none=True)
    if "projects" in changes:
        changes["projects"] = None if changes["projects"] == "all" else changes["projects"]
        _known_projects(db, changes["projects"])
    demoting = "role" in changes and changes["role"] != (db.users.find_one({"_id": user_id}) or {}).get("role")
    if user_id == owner["_id"] and (demoting or changes.get("is_active") is False):
        raise HTTPException(409, "you cannot demote or disable yourself")
    if (demoting and changes["role"] != "owner" or changes.get("is_active") is False) and _last_owner(db, user_id):
        raise HTTPException(409, "this is the last owner: make someone else an owner first")
    if not changes:
        raise HTTPException(422, "nothing to change")
    changes["updated_at"] = utcnow()
    result = db.users.find_one_and_update({"_id": user_id}, {"$set": changes}, return_document=True)
    if result is None:
        raise HTTPException(404, "user not found")
    if changes.get("is_active") is False:
        db.sessions.delete_many({"user_id": user_id})
    return UserOut.from_document(result)


@router.post("/users/{user_id}/password", status_code=204)
def reset_user_password(user_id: str, body: NewPassword, owner: OwnerUser, db: DB):
    """Set someone's password (there is no email): they sign in with it, and their sessions end."""
    if set_password(db, user_id, body.password) is None:
        raise HTTPException(404, "user not found")


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: str, owner: OwnerUser, db: DB):
    if user_id == owner["_id"]:
        raise HTTPException(409, "you cannot delete yourself")
    if _last_owner(db, user_id):
        raise HTTPException(409, "this is the last owner: make someone else an owner first")
    if db.users.delete_one({"_id": user_id}).deleted_count == 0:
        raise HTTPException(404, "user not found")
    db.sessions.delete_many({"user_id": user_id})


# ---- projects

def project_out(db: Database, project: dict[str, Any]) -> ProjectOut:
    return ProjectOut(
        id=project["_id"],
        name=project["name"],
        description=project.get("description"),
        website=project.get("website"),
        number=project["number"],
        status=project["status"],
        key_paths={f.value: coin_path(project["number"], f) for f in Family},
        xpubs={f.value: (project.get("xpubs") or {}).get(f.value) for f in Family},
        treasury={f.value: (project.get("treasury") or {}).get(f.value) for f in Family},
        chains=project.get("chains") or {},
        products=project.get("products") or [],
        deposit_ttl_minutes=setting(project, "deposit_ttl_minutes"),
        remainder_window_minutes=setting(project, "remainder_window_minutes"),
        amount_tolerance_bps=setting(project, "amount_tolerance_bps"),
        allowed_ips=project.get("allowed_ips") or [],
        events_acked=project.get("events_acked", 0),
        created_at=utc(project["created_at"]),
        updated_at=utc(project.get("updated_at") or project["created_at"]),
    )


def locked_out(project: dict[str, Any]) -> ProjectOut:
    """A project the user may not open: its name and number, so they know it is there, and nothing else."""
    return ProjectOut(
        id=project["_id"], name=project["name"], description=None, website=None, number=project["number"], status="locked",
        key_paths={}, xpubs={}, treasury={}, chains={}, products=[], deposit_ttl_minutes=0,
        remainder_window_minutes=0, amount_tolerance_bps=0, allowed_ips=[], events_acked=0,
        created_at=None, updated_at=None,
    )


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(user: CurrentUser, db: DB):
    """Every project; one outside the user's projects is listed as locked, with only its name and number."""
    return [
        project_out(db, p) if can_see(user, p["_id"]) else locked_out(p)
        for p in db.projects.find().sort("created_at", ASCENDING)
    ]


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(body: CreateProject, owner: OwnerUser, db: DB):
    """Owners only. A new project with a random number; a sweeper gives it its keys on its next pass
    (or paste them from `python -m api.keys project <number>`)."""
    fields = body.model_dump(exclude_none=True)
    fields["products"] = [p.model_dump() for p in body.products]
    return project_out(db, new_project(db, fields, owner["_id"]))


@router.get("/projects/{project_id}", response_model=ProjectOut)
def read_project(project_id: str, user: CurrentUser, db: DB):
    return project_out(db, _project_or_404(db, project_id, user))


# where the money goes and who may call the API: an owner's to change
OWNER_FIELDS = ("treasury", "allowed_ips")


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: str, body: ProjectFields, admin: AdminUser, db: DB):
    """Admins change everything but the treasury and allowed IPs, which only an owner changes (an admin
    may send them back unchanged)."""
    project = _project_or_404(db, project_id, admin)
    changes = body.model_dump(exclude_unset=True)
    if "products" in changes:
        changes["products"] = [p.model_dump() for p in body.products or []]
    for name in ("name", "products", "treasury", "chains", "allowed_ips"):
        if name in changes and changes[name] is None:
            raise HTTPException(422, f"{name} cannot be empty")
    if admin["role"] != "owner":
        stored = {
            "treasury": {f.value: (project.get("treasury") or {}).get(f.value) for f in Family},
            "allowed_ips": project.get("allowed_ips") or [],
        }
        if "treasury" in changes:
            changes["treasury"] = {f.value: changes["treasury"].get(f.value) for f in Family}
        if any(name in changes and changes[name] != stored[name] for name in OWNER_FIELDS):
            raise HTTPException(403, "Only an owner changes the treasury and allowed IPs")
    if not changes:
        raise HTTPException(422, "nothing to change")
    changes["updated_at"] = utcnow()
    project = db.projects.find_one_and_update({"_id": project_id}, {"$set": changes}, return_document=True)
    return project_out(db, project)


@router.post("/projects/{project_id}/keys", response_model=ProjectOut)
def set_project_keys(project_id: str, body: ProjectKeys, owner: OwnerUser, db: DB):
    """The project's xpubs. A key can only change while no deposit used it: its addresses would differ."""
    project = _project_or_404(db, project_id, owner)
    changes = {}
    for family in Family:
        xpub = getattr(body, family.value)
        if not xpub:
            continue
        xpub = xpub.strip()
        try:
            check_project_xpub(family, xpub)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        current = (project.get("xpubs") or {}).get(family.value)
        if current and current != xpub and db.deposits.count_documents({"project_id": project_id, "family": family.value}, limit=1):
            raise HTTPException(409, f"the {family} key is already used by deposits and cannot change")
        changes[f"xpubs.{family.value}"] = xpub
    if project["status"] == ProjectStatus.AWAITING_KEYS:
        changes["status"] = ProjectStatus.ACTIVE
    changes["updated_at"] = utcnow()
    project = db.projects.find_one_and_update({"_id": project_id}, {"$set": changes}, return_document=True)
    return project_out(db, project)


@router.post("/projects/{project_id}/status", response_model=ProjectOut)
def set_project_status(
    project_id: str, admin: AdminUser, db: DB,
    status: Annotated[Literal["active", "disabled"], Query()],
):
    """Disable a project (its keys stop working; its deposits are still watched and swept) or enable it."""
    project = _project_or_404(db, project_id, admin)
    if status == ProjectStatus.ACTIVE and not any((project.get("xpubs") or {}).values()):
        raise HTTPException(409, "add the project's keys first")
    project = db.projects.find_one_and_update(
        {"_id": project_id}, {"$set": {"status": status, "updated_at": utcnow()}}, return_document=True
    )
    return project_out(db, project)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, owner: OwnerUser, db: DB, confirm: Annotated[str, Query()]):
    """Owners only, and only with the project's name as `confirm`: the project, its API keys, its deposit
    addresses and its events are gone. Its addresses are no longer watched or swept; coins still on them
    stay on chain, reachable only with the master key."""
    project = _project_or_404(db, project_id, owner)
    if confirm != project["name"]:
        raise HTTPException(422, "confirm with the project's exact name")
    db.api_keys.delete_many({"project_id": project_id})
    db.deposits.delete_many({"project_id": project_id})
    db.events.delete_many({"project_id": project_id})
    db.projects.delete_one({"_id": project_id})
    db.users.update_many({"projects": project_id}, {"$pull": {"projects": project_id}})


@router.get("/projects/{project_id}/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(project_id: str, user: CurrentUser, db: DB):
    _project_or_404(db, project_id, user)
    found = db.api_keys.find({"project_id": project_id}).sort("created_at", DESCENDING)
    return [ApiKeyOut.from_document(k) for k in found]


@router.post("/projects/{project_id}/api-keys", response_model=CreatedApiKey, status_code=201)
def create_api_key(project_id: str, body: NewApiKey, owner: OwnerUser, db: DB):
    """Owners only. The key is in this reply only; HDPay keeps a hash of it."""
    _project_or_404(db, project_id, owner)
    doc, plain = apikeys.new_key(db, project_id, body.kind, body.name, owner["_id"])
    return CreatedApiKey(**ApiKeyOut.from_document(doc).model_dump(), key=plain)


@router.delete("/projects/{project_id}/api-keys/{key_id}", response_model=ApiKeyOut)
def revoke_api_key(project_id: str, key_id: str, owner: OwnerUser, db: DB):
    """Owners only."""
    _project_or_404(db, project_id, owner)
    doc = db.api_keys.find_one_and_update(
        {"_id": key_id, "project_id": project_id, "revoked_at": None},
        {"$set": {"revoked_at": utcnow()}},
        return_document=True,
    )
    if doc is None:
        raise HTTPException(404, "no such active key")
    return ApiKeyOut.from_document(doc)


@router.get("/projects/{project_id}/events", response_model=list[EventOut])
def recent_events(project_id: str, user: CurrentUser, db: DB, limit: Annotated[int, Query(ge=1, le=200)] = 50):
    """The project's latest events, newest first, to see what its backend was sent."""
    _project_or_404(db, project_id, user)
    found = db.events.find({"project_id": project_id}).sort("seq", DESCENDING).limit(limit)
    return [EventOut(**events.public(e)) for e in found]


@router.get("/projects/{project_id}/funders", response_model=list[FunderBalancesOut])
def project_funders(project_id: str, user: CurrentUser):
    """The project's fee funders, with their native balance on the chains it accepts.

    Read live from each chain's RPC, so it is a separate call: a slow node never delays the rest.
    """
    db = get_db()
    project = _project_or_404(db, project_id, user)
    out = []
    for family in Family:
        xpub = (project.get("xpubs") or {}).get(family.value)
        if not xpub:
            continue
        address = funder_address(family, xpub)
        chains = [c for c, _ in accepted(project) if c.family == family]
        results = native_balances([(c, address) for c in chains])
        out.append(FunderBalancesOut(
            family=family,
            path=funder_path(project["number"], family),
            address=address,
            balances=[
                FunderBalance(
                    chain=c.key, chain_name=c.name, symbol=c.native_symbol, decimals=c.native_decimals,
                    balance=None if balance is None else str(balance), error=error,
                    address_url=c.explorer_address(address),
                )
                for c, (balance, error) in zip(chains, results)
            ],
        ))
    return out


@router.get("/projects/{project_id}/treasury", response_model=list[TreasuryOut])
def project_treasury(project_id: str, user: CurrentUser):
    """The project's treasuries with their live balance of every asset it accepts."""
    db = get_db()
    project = _project_or_404(db, project_id, user)
    out = []
    for family in Family:
        address = (project.get("treasury") or {}).get(family.value)
        rows = [(c, asset) for c, assets in accepted(project) if c.family == family for asset in assets]
        if not rows and not address:
            continue
        results = asset_balances([(c, asset, address) for c, asset in rows]) if address else []
        out.append(TreasuryOut(
            family=family,
            address=address,
            balances=[
                TreasuryBalance(
                    chain=c.key, chain_name=c.name, asset=asset, decimals=c.decimals(asset),
                    balance=None if value is None else str(value), error=error,
                    address_url=c.explorer_address(address),
                )
                for (c, asset), (value, error) in zip(rows, results)
            ],
        ))
    return out


# ---- deposits

def sweep_stage(d: Deposit) -> str:
    """Where a credited deposit is in the sweep: waiting, topping_up, sweeping or held."""
    if d.sweep_hold:
        return "held"
    if d.sweep_tx_hash:
        return "sweeping"
    if d.fee_tx_hash:
        return "topping_up"
    return "waiting"


@router.get("/sweeper", response_model=SweeperOut)
def sweeper(
    owner: OwnerUser, db: DB,
    recent: Annotated[int, Query(ge=1, le=100)] = 20,     # sweeps per page
    recent_page: Annotated[int, Query(ge=1)] = 1,
    queue_size: Annotated[int, Query(ge=1, le=100)] = 20,
    queue_page: Annotated[int, Query(ge=1)] = 1,
):
    """A page of the sweep queue (held first, then oldest first), a page of the sweeps (newest first),
    the count of each (their totals) and each chain's sweeper check-in."""
    projects = projects_by_id(db)
    credited = {"status": Status.CONFIRMED.value}
    counts = {"waiting": 0, "topping_up": 0, "sweeping": 0, "held": 0}
    for doc in db.deposits.find(credited, {"sweep_hold": 1, "sweep_tx_hash": 1, "fee_tx_hash": 1}):
        counts[sweep_stage(Deposit(doc))] += 1
    # a held deposit needs someone: it heads the queue, on the first page
    queue = [Deposit(d) for d in db.deposits.find(credited)
             .sort([("sweep_hold", DESCENDING), ("confirmed_at", ASCENDING), ("_id", ASCENDING)])
             .skip((queue_page - 1) * queue_size).limit(queue_size)]
    counts["swept"] = db.deposits.count_documents({"status": Status.SWEPT.value})
    swept = [Deposit(d) for d in db.deposits.find({"status": Status.SWEPT.value}).sort([("swept_at", DESCENDING), ("created_at", DESCENDING), ("_id", DESCENDING)])
             .skip((recent_page - 1) * recent).limit(recent)]
    sweepers = [
        SweeperState(
            chain=c.key, chain_name=c.name, family=c.family,
            heartbeat_at=_iso_timestamp(get_counter(db, f"sweeper_heartbeat:{c.key}")),
        )
        for c in _chains_in_use(db)
    ]
    return SweeperOut(
        counts=counts,
        sweepers=sweepers,
        queue=[_admin_deposit(d, projects) for d in queue],
        recent=[_admin_deposit(d, projects) for d in swept],
        recent_page=recent_page,
        recent_size=recent,
        queue_page=queue_page,
        queue_size=queue_size,
    )


@router.get("/deposits", response_model=AdminDepositList)
def list_deposits(
    user: CurrentUser,
    db: DB,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[str | None, Query(description="Deposit id, account id, product, address or transaction hash")] = None,
    project_id: str | None = None,
    chain: str | None = None,
    asset_type: Annotated[
        str | None,
        Query(alias="type", pattern=r"^(native|token|[A-Z][A-Z0-9-]{1,15})$",
              description="native, a token standard (ERC20, BEP-20, TRC20), or token for any"),
    ] = None,
    asset: str | None = None,
    status: Status | None = None,
    difference: Annotated[Literal["over", "under"] | None, Query(description="Overpaid or underpaid")] = None,
):
    projects = projects_by_id(db)
    conditions: list[dict[str, Any]] = []
    if (scope := _in_scope(user)):
        conditions.append(scope)
    if search:
        term = search.strip()
        exact = {"$regex": f"^{re.escape(term)}$", "$options": "i"}
        named = [
            p["value"] for project in projects.values() for p in project.get("products") or []
            if term.lower() in p["name"].lower()
        ]
        conditions.append({"$or": [
            {"_id": term},
            {"account_id": term},
            {"address": exact},
            {"tx_hash": exact},
            {"sweep_tx_hash": exact},
            {"product": {"$regex": re.escape(term), "$options": "i"}},
            {"product": {"$in": named}},
        ]})
    if project_id:
        conditions.append({"project_id": project_id})
    if chain:
        conditions.append({"chain": chain})
    if asset_type:
        # native: the asset is its chain's own coin; anything else is a token, named by its chain's standard
        native = [{"chain": c.key, "asset": c.native_symbol} for c in CHAINS.values()]
        conditions.append({"$or": native} if asset_type == "native" else {"$nor": native})
        if asset_type not in ("native", "token"):
            conditions.append({"chain": {"$in": [c.key for c in CHAINS.values() if c.token_standard == asset_type]}})
    if asset:
        conditions.append({"asset": asset})
    if status:
        conditions.append({"status": status.value})
    if difference:
        conditions.append({"difference": difference})
    query = {"$and": conditions} if conditions else {}

    total = db.deposits.count_documents(query)
    # newest first, always; the id breaks ties so paging never repeats or skips a row
    cursor = db.deposits.find(query).sort([("created_at", DESCENDING), ("_id", DESCENDING)]).skip(skip).limit(limit)
    items = [_admin_deposit(Deposit(doc), projects) for doc in cursor]
    return AdminDepositList(total=total, skip=skip, limit=limit, items=items)


@router.get("/deposits/{deposit_id}", response_model=AdminDepositOut)
def get_deposit(deposit_id: str, user: CurrentUser, db: DB):
    deposit = _deposit_or_404(db, deposit_id, user)
    return AdminDepositOut.from_deposit(deposit, get_project(db, deposit.project_id))


# a check can be asked for while the money may still be on its way or in question; a paid deposit is done
CHECKABLE = (Status.PENDING, Status.EXPIRED, Status.CANCELLED, Status.DETECTED, Status.PARTIAL, Status.REVIEW)


@router.post("/deposits/{deposit_id}/check", response_model=AdminDepositOut)
def check_deposit(deposit_id: str, admin: AdminUser, db: DB):
    """Support: have the chain's watcher rescan this address from just before the deposit was opened.
    It counts any payment not counted yet, judged by the time of its block (so a deposit in review whose
    rest came in time is credited), and compares the balance for coins sent through a contract."""
    deposit = _deposit_or_404(db, deposit_id, admin)
    if deposit.status not in CHECKABLE:
        raise HTTPException(409, f"deposit is {deposit.status.value}: it is paid already")
    deposit.check = {"status": "waiting", "requested_at": utcnow(), "requested_by": admin.get("name") or admin["email"]}
    deposit.save(db)
    return AdminDepositOut.from_deposit(deposit, get_project(db, deposit.project_id))


@router.get("/deposits/{deposit_id}/chain", response_model=ChainView)
def deposit_on_chain(deposit_id: str, user: CurrentUser, db: DB):
    """The address's balance now, read from the chain, and when the chain's watcher last ran."""
    deposit = _deposit_or_404(db, deposit_id, user)
    seen = get_counter(db, f"heartbeat:{deposit.chain}")
    try:
        amount, error = str(balance(CHAINS[deposit.chain], deposit.asset, deposit.address)), None
    except Exception as exc:
        amount, error = None, rpc_problem(exc)
    return ChainView(balance=amount, error=error, watcher_seen_at=_iso_timestamp(seen))


@router.post("/deposits/{deposit_id}/credit", response_model=AdminDepositOut)
def credit_reviewed_deposit(deposit_id: str, admin: AdminUser, db: DB):
    """Support action: credit an underpaid deposit as it is, after a person checked it."""
    deposit = _deposit_or_404(db, deposit_id, admin)
    if deposit.status not in (Status.REVIEW, Status.PARTIAL):
        raise HTTPException(409, f"deposit is {deposit.status.value}, not review or partial")
    deposit.status = Status.CONFIRMED
    deposit.confirmed_at = utcnow()
    deposit.save(db)
    project = get_project(db, deposit.project_id)
    notify_status(db, deposit, project)
    return AdminDepositOut.from_deposit(deposit, project)


@router.post("/deposits/{deposit_id}/reopen", response_model=AdminDepositOut)
def reopen_reviewed_deposit(deposit_id: str, admin: AdminUser, db: DB, body: ReopenDeposit | None = None):
    """Support action: give a deposit a fresh window, the project gets deposit.reopened to pass on.

    In review (underpaid, the rest never came): back to partial, waiting for the rest. Expired (nothing
    came in its window): back to pending, waiting for the payment as if the address were new, judged
    on time against the new window. Support may set the remaining amount (the expected amount becomes
    received + remaining), the window and the product; they default to the current shortfall, the
    project's remainder window (review) or payment window (expired), and the current product.

    A different priced product brings its price: the deposit expects it (less its discount, by the same
    percent, while that counts). A product discount that ended before the deposit was paid is due in full:
    the rest becomes the full price less what came, unless keep_discount, which honors the discount (and
    holds it from then on).
    """
    body = body or ReopenDeposit()
    deposit = _deposit_or_404(db, deposit_id, admin)
    project = get_project(db, deposit.project_id)
    if deposit.status not in (Status.REVIEW, Status.EXPIRED, Status.CANCELLED):
        raise HTTPException(409, f"deposit is {deposit.status.value}: only one in review, expired or cancelled can be reopened")
    expired = deposit.status in (Status.EXPIRED, Status.CANCELLED)
    if not deposit.expected_amount and not expired:
        raise HTTPException(409, "deposit accepts any amount, so there is no rest to wait for")
    window = timedelta(minutes=body.minutes) if body.minutes else None
    reopen(db, deposit, project, window, body.remaining_amount, body.product, body.keep_discount)
    return AdminDepositOut.from_deposit(deposit, project)


@router.delete("/deposits/{deposit_id}", status_code=204)
def delete_deposit(deposit_id: str, owner: OwnerUser, db: DB):
    """Owners only: the deposit address is gone from HDPay, so it is no longer watched or swept. Coins
    still on it stay on chain, reachable only with the master key; its path is in the address's events."""
    deposit = _deposit_or_404(db, deposit_id, owner)
    db.deposits.delete_one({"_id": deposit.id})


@router.post("/deposits/{deposit_id}/release", response_model=AdminDepositOut)
def release_hold(deposit_id: str, admin: AdminUser, db: DB):
    """Support action: let the sweeper try a held deposit again, after the cause was checked."""
    deposit = _deposit_or_404(db, deposit_id, admin)
    if not deposit.sweep_hold:
        raise HTTPException(409, "deposit is not on hold")
    deposit.sweep_hold = None
    deposit.save(db)
    return AdminDepositOut.from_deposit(deposit, get_project(db, deposit.project_id))
