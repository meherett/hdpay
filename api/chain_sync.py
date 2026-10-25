"""
The chains file, kept in the database (collection `chains`) and brought up to date when the API starts.

Every network and token the file has ever listed stays in the database, so a deposit opened in a token
that was later taken out of the file still has its details (contract, decimals, name, explorer).
Each carries `supported`: true while chains.yaml lists it, false once it was taken out (it is never
deleted), with `supported_since` and `supported_until`, so the deposit history shows which coins were
accepted then and are not now.

A token that is new, or whose contract or decimals changed, is checked against its contract on the
chain before it is applied: the chain is the judge.

    the contract's decimals match the file        applied
    they do not                                    refused: the previous values stay, and no new
                                                   deposit can be opened in the token until the
                                                   file is fixed and the API restarted
    the contract cannot be read (RPC down)         not applied yet: checked again on the next start

A contract that changes keeps its old one in the token's history: deposits opened before the change
keep receiving, being watched and being swept at the contract they were opened with.
"""

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from pymongo.database import Database

from .chains import CHAINS, Chain, Family, Token
from .db import utcnow

log = logging.getLogger("hdpay.chains")

# chains the file no longer lists, from the database: their deposits still read right
_removed: dict[str, Chain] = {}


def chain_for(key: str, family: str | None = None) -> Chain:
    """The chain of a deposit: from the file, else as the database remembers it, else a bare stand-in
    (a network never seen by this database), so a deposit always displays."""
    chain = CHAINS.get(key) or _removed.get(key)
    if chain is not None:
        return chain
    return Chain(key=key, family=Family(family or "evm"), name=key, native_symbol="", native_decimals=18,
                 confirmations=1, explorer="")


def same_contract(chain: Chain, a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a == b
    return a.lower() == b.lower() if chain.family == Family.EVM else a == b


def _verify(chain: Chain, token: Token) -> int:
    from .balances import token_metadata

    return token_metadata(chain, token)[0]


def sync_chains(
    db: Database,
    chains: Mapping[str, Chain] | None = None,
    verify: Callable[[Chain, Token], int] | None = None,
) -> list[tuple[str, str]]:
    """Bring the database's copy of the chains up to date with the file; returns (level, line) for the log.
    `verify` returns a token contract's decimals as the chain reports them."""
    from .balances import rpc_problem

    verify = verify or _verify
    chains = dict(chains if chains is not None else CHAINS.items())
    now = utcnow()
    lines: list[tuple[str, str]] = []
    stored = {doc["_id"]: doc for doc in db.chains.find()}

    for key, c in chains.items():
        doc = _upgraded(stored[key]) if key in stored else None
        before = {s: _upgraded(t) for s, t in ((doc or {}).get("tokens") or {}).items()}
        tokens: dict[str, Any] = {}
        for symbol, t in c.tokens.items():
            prev = before.get(symbol)
            if prev and not prev["supported"]:
                lines.append(("info", f"{key} {symbol}: back in chains.yaml, supported again"))
            if prev and prev.get("decimals") is not None and same_contract(c, prev["contract"], t.contract) \
                    and prev["decimals"] == t.decimals:
                tokens[symbol] = prev | _supported(prev, now) | {"refused": None}
                continue
            if prev is None or prev.get("decimals") is None:
                what = "added"
            elif not same_contract(c, prev["contract"], t.contract):
                what = f"contract changed {prev['contract']} -> {t.contract}"
            else:
                what = f"decimals changed {prev['decimals']} -> {t.decimals}"
            try:
                on_chain = verify(c, t)
            except Exception as exc:
                lines.append(("warning", f"{key} {symbol}: {what}, but its contract could not be read "
                                         f"({rpc_problem(exc)}): not applied yet, checked again on the next start"))
                if prev:
                    tokens[symbol] = prev | _supported(prev, now)
                continue
            if on_chain == t.decimals:
                history = list((prev or {}).get("history") or [])
                if prev and prev.get("decimals") is not None and (
                    not same_contract(c, prev["contract"], t.contract) or prev["decimals"] != t.decimals
                ):
                    # deposits opened before the change keep what they were opened with
                    history.append({"contract": prev["contract"], "decimals": prev["decimals"], "until": now})
                tokens[symbol] = {"contract": t.contract, "decimals": t.decimals, "refused": None,
                                  "verified_at": now, "history": history, **_supported(prev, now)}
                lines.append(("info", f"{key} {symbol}: {what}: the contract confirms {on_chain} decimals, applied"))
            else:
                refused = {"contract": t.contract, "decimals": t.decimals, "on_chain": on_chain, "at": now}
                tokens[symbol] = (prev or {"contract": t.contract, "decimals": None, "history": []}) | {
                    "refused": refused, **_supported(prev, now),
                }
                lines.append(("error", f"{key} {symbol}: {what}, but contract {t.contract} has {on_chain} decimals, "
                                       f"not {t.decimals}: refused. The previous values stay and no new deposit can be "
                                       f"opened in it: fix chains.yaml and restart the API"))
        for symbol, prev in before.items():
            if symbol in c.tokens:
                continue
            if prev["supported"]:
                lines.append(("info", f"{key} {symbol}: no longer in chains.yaml; kept as not supported for its deposits"))
            tokens[symbol] = prev | _unsupported(prev, now)

        new = {
            "_id": key, "family": c.family.value, "name": c.name, "chain_id": c.chain_id,
            "native": {"symbol": c.native_symbol, "decimals": c.native_decimals},
            "confirmations": c.confirmations, "explorer": c.explorer, "token_standard": c.standard,
            "tokens": tokens, **_supported(doc, now),
            "added_at": (doc or {}).get("added_at") or now, "updated_at": now,
        }
        if doc is None:
            lines.append(("info", f"{key}: new network, {c.name} ({', '.join(c.assets())})"))
        else:
            changed = [f for f in ("name", "explorer", "confirmations", "token_standard", "chain_id", "native")
                       if doc.get(f) != new[f]]
            if not doc["supported"]:
                lines.append(("info", f"{key}: back in chains.yaml, supported again"))
            if changed:
                lines.append(("info", f"{key}: {', '.join(changed)} updated"))
        db.chains.replace_one({"_id": key}, new, upsert=True)

    for key, doc in stored.items():
        if key in chains:
            continue
        doc = _upgraded(doc)
        if doc["supported"] or "listed" in stored[key]:
            tokens = {s: _upgraded(t) for s, t in (doc.get("tokens") or {}).items()}
            db.chains.update_one({"_id": key}, {
                "$set": {**_unsupported(doc, now), "updated_at": now,
                         "tokens": {s: t | _unsupported(t, now) for s, t in tokens.items()}},
                "$unset": {"listed": ""},
            })
            if doc["supported"]:
                lines.append(("info", f"{key}: no longer in chains.yaml; kept as not supported for its deposits"))

    filled = fill_deposits(db)
    if filled:
        lines.append(("info", f"{filled} deposit(s) given their token's contract and decimals"))
    load_removed(db, chains)
    for level, line in lines:
        getattr(log, level)("chains.yaml: %s", line)
    if not lines:
        log.info("chains.yaml: the database is up to date (%d networks)", len(chains))
    return lines


def _upgraded(entry: dict[str, Any]) -> dict[str, Any]:
    """A network or token as stored, with `supported` (an earlier version named it `listed`)."""
    entry = dict(entry)
    entry["supported"] = entry.pop("listed", entry.get("supported", True))
    return entry


def _supported(prev: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    """In chains.yaml: supported since it was first added, or since it came back."""
    if prev and prev.get("supported"):
        since = prev.get("supported_since") or prev.get("added_at") or prev.get("verified_at") or now
    else:
        since = now
    return {"supported": True, "supported_since": since, "supported_until": None}


def _unsupported(prev: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Taken out of chains.yaml: kept, and not supported since then."""
    until = now if prev.get("supported") else (prev.get("supported_until") or now)
    return {"supported": False, "supported_until": until}


def _as_of(token: dict[str, Any], opened: datetime) -> tuple[str | None, int | None]:
    """The token's contract and decimals when a deposit was opened: from its history, or its current values."""
    for entry in sorted(token.get("history") or [], key=lambda e: e["until"]):
        if opened < entry["until"]:
            return entry["contract"], entry["decimals"]
    return token.get("contract"), token.get("decimals")


def fill_deposits(db: Database) -> int:
    """Deposits from before they kept their token's contract and decimals get them from the database's
    copy of the chains, as they were when each deposit opened."""
    filled = 0
    docs = {doc["_id"]: doc for doc in db.chains.find()}
    query = {"$or": [{"decimals": {"$exists": False}}, {"contract": {"$exists": False}}]}
    for d in db.deposits.find(query, {"chain": 1, "asset": 1, "created_at": 1, "decimals": 1}):
        chain = docs.get(d.get("chain"))
        if chain is None:
            continue
        changes: dict[str, Any] = {}
        if d.get("asset") == chain["native"]["symbol"]:
            changes = {"contract": None, "decimals": d.get("decimals", chain["native"]["decimals"])}
        elif d.get("asset") in chain["tokens"]:
            contract, decimals = _as_of(chain["tokens"][d["asset"]], d["created_at"])
            if contract is None or decimals is None:
                continue
            changes = {"contract": contract}
            if d.get("decimals") is None:
                changes["decimals"] = decimals
        if changes:
            db.deposits.update_one({"_id": d["_id"]}, {"$set": changes})
            filled += 1
    return filled


def load_removed(db: Database, listed: Mapping[str, Chain]) -> None:
    """Remember the networks the file no longer lists, with all their tokens, for their deposits."""
    removed = {}
    for doc in db.chains.find({"_id": {"$nin": list(listed)}}):
        removed[doc["_id"]] = Chain(
            key=doc["_id"], family=Family(doc["family"]), name=doc["name"],
            native_symbol=doc["native"]["symbol"], native_decimals=doc["native"]["decimals"],
            confirmations=doc["confirmations"], explorer=doc["explorer"], chain_id=doc.get("chain_id"),
            tokens={s: Token(s, t["contract"], t["decimals"]) for s, t in (doc.get("tokens") or {}).items()
                    if t.get("decimals") is not None},
            standard=doc.get("token_standard"),
        )
    _removed.clear()
    _removed.update(removed)


def confirmed_tokens(chain: Chain, db: Database | None = None) -> set[str]:
    """The chain's tokens already checked against their contracts, with the contract and decimals the
    file has now: a watcher or sweeper starting need not ask the chain about them again. None when the
    database cannot be read: then every token is checked."""
    from .db import get_db

    try:
        doc = (db if db is not None else get_db()).chains.find_one({"_id": chain.key}) or {}
    except Exception:
        log.warning("%s: the database's copy of the chains could not be read; checking every token", chain.key)
        return set()
    stored = doc.get("tokens") or {}
    return {
        symbol for symbol, t in chain.tokens.items()
        if (s := stored.get(symbol)) and s.get("verified_at") and not s.get("refused")
        and same_contract(chain, s.get("contract"), t.contract) and s.get("decimals") == t.decimals
    }


def refused(db: Database, chain_key: str, asset: str) -> str | None:
    """Why no deposit can be opened in this token now, or None."""
    doc = db.chains.find_one({"_id": chain_key}, {f"tokens.{asset}": 1})
    entry = ((doc or {}).get("tokens") or {}).get(asset) or {}
    r = entry.get("refused")
    if not r:
        return None
    return (f"{asset} on {chain_key} is not available: chains.yaml says {r['decimals']} decimals for contract "
            f"{r['contract']}, but the contract has {r['on_chain']}. The operator must fix chains.yaml")
