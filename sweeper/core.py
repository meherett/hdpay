"""
Chain-independent sweeper loop. For every confirmed deposit on the chain:

  1. a sweep was sent      -> mark SWEPT once it succeeds; hold the deposit if it failed
  2. a fee top-up was sent -> wait until it lands; hold the deposit if it failed
  3. otherwise             -> let the chain sweeper top up fees or send the funds

Funds go to the deposit's project's treasury, and fees come from that project's fee funder.

A sweeper also gives each new project its keys: it holds the master key, so it derives the project's
two xpubs (m/{number}'/{coin}') and activates the project, and the master key never leaves this host.
Each deposit is saved right after its step, so a broadcast transaction hash is never lost.
A held deposit is skipped until support releases it in the admin, so a transaction that failed
on chain is never resent in a loop.
"""

import logging
import time
from typing import Any

from pymongo import ASCENDING
from pymongo.database import Database

from api.chains import Family, TxStatus, follow, handles
from api.db import Deposit, Status, create_indexes, find_deposits, get_db, set_counter, utcnow
from api.deposits import notify_status
from api.keys import project_xpub
from api.projects import ProjectStatus, projects_by_id
from .base import Sweeper

log = logging.getLogger("hdpay.sweeper")
SWEEP_INTERVAL_SECONDS = 30


def describe(exc: Exception) -> str | None:
    """What a node or the network said, for an error worth one line; None for a bug, which keeps its
    traceback."""
    response = getattr(exc, "rpc_response", None)
    if isinstance(response, dict) and isinstance(response.get("error"), dict):
        return response["error"].get("message") or str(exc)
    module = type(exc).__module__
    if module.startswith(("web3", "tronpy", "requests", "httpx", "urllib3")) or isinstance(exc, (ConnectionError, TimeoutError)):
        return str(exc) or type(exc).__name__
    return None


def hold(d: Deposit, reason: str) -> None:
    d.sweep_hold = reason[:300]
    log.error("holding deposit %s (%s): %s. Not retrying; check it, then release it in the admin.", d.id, d.address, reason)


def advance(sweeper: Sweeper, d: Deposit, project: dict[str, Any]) -> None:
    if d.sweep_tx_hash:
        status = sweeper.tx_status(d.sweep_tx_hash)
        if status == TxStatus.OK:
            d.status = Status.SWEPT
            d.swept_at = utcnow()
            log.info("swept %s %s from %s in %s", d.received_amount, d.asset, d.address, d.sweep_tx_hash)
        elif status == TxStatus.FAILED:
            hold(d, f"sweep {d.sweep_tx_hash} failed on chain")
            d.sweep_tx_hash = None
        return

    if d.fee_tx_hash:
        status = sweeper.tx_status(d.fee_tx_hash)
        if status == TxStatus.UNKNOWN:
            return
        if status == TxStatus.FAILED:
            hold(d, f"fee top-up {d.fee_tx_hash} failed on chain")
            d.fee_tx_hash = None
            return
        d.fee_tx_hash = None

    if not handles(sweeper.chain, d):
        hold(d, f"{d.asset} on {sweeper.chain.name}: its contract is not known; add it to chains.yaml, then release")
        return

    treasury = sweeper.treasury(project)
    if not treasury:
        log.warning("project %s has no %s treasury yet; deposit %s waits", project.get("name"), sweeper.chain.family, d.id)
        return
    sweeper.sweep(d, project, treasury)


def issue_keys(db: Database, master_xprv: str) -> None:
    """Projects still waiting for their keys get them from the master key, and become active. Only those:
    a key already set (pasted by hand) is never replaced, since its deposits' addresses depend on it."""
    for project in db.projects.find({"status": ProjectStatus.AWAITING_KEYS}):
        xpubs = {f"xpubs.{f.value}": project_xpub(master_xprv, project["number"], f) for f in Family}
        done = db.projects.update_one(
            {"_id": project["_id"], "status": ProjectStatus.AWAITING_KEYS},   # another sweeper may be first
            {"$set": {**xpubs, "status": ProjectStatus.ACTIVE, "updated_at": utcnow()}},
        )
        if done.modified_count:
            log.info("project %s (%s) has its keys and is active", project["name"], project["number"])


def sweep_once(db: Database, sweeper: Sweeper) -> None:
    master_xprv = getattr(sweeper, "master_xprv", None)
    if master_xprv:
        try:
            issue_keys(db, master_xprv)
        except Exception:
            log.exception("giving new projects their keys failed")
    projects = projects_by_id(db)
    confirmed = find_deposits(
        db,
        {"chain": sweeper.chain.key, "status": Status.CONFIRMED.value, "sweep_hold": None},
        sort=[("confirmed_at", ASCENDING), ("_id", ASCENDING)],
    )
    for d in confirmed:
        project = projects.get(d.project_id)
        if project is None:
            log.error("deposit %s belongs to a project that no longer exists", d.id)
            continue
        try:
            advance(sweeper, d, project)
        except Exception as exc:
            reason = describe(exc)
            if reason is None:
                log.exception("sweeping deposit %s (%s) failed", d.id, d.address)
            else:
                log.error("sweeping deposit %s (%s) failed, trying again next pass: %s", d.id, d.address, reason)
        d.save(db)
        if d.status == Status.SWEPT:
            notify_status(db, d, project)
    # read by the admin's Sweeper page: when this sweeper last made a pass
    set_counter(db, f"sweeper_heartbeat:{sweeper.chain.key}", int(time.time()))


def run(sweeper: Sweeper, build=None) -> None:
    """`build` makes a sweeper for a chain, so an edit to the chains file is applied while running."""
    log.info("sweeping %s to each project's treasury", sweeper.chain.name)
    db = get_db()
    create_indexes(db)
    while True:
        try:
            if build:
                sweeper = follow(sweeper, build)
            sweep_once(db, sweeper)
        except Exception as exc:
            reason = describe(exc)
            if reason is None:
                log.exception("sweep loop failed")
            else:
                log.error("sweep pass on %s failed, trying again in %ss: %s", sweeper.chain.name, SWEEP_INTERVAL_SECONDS, reason)
        time.sleep(SWEEP_INTERVAL_SECONDS)
