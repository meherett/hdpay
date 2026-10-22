"""
Project API keys. A project's backend sends one with every request, and when it opens the event
WebSocket:

    Authorization: Bearer hdp_sk_...

    hdp_sk_  secret: everything the project can do, including its events. Server side only.
    hdp_pk_  publishable: list chains and products, open deposits and read them; for a checkout
             page running in a browser, like the hdpay web sample.

A key is shown once, when it is created; only its SHA-256 and its first characters are stored.
A project can hold several, so a key is replaced without downtime: create the new one, switch,
then revoke the old one. If the project lists allowed IPs, keys work only from those.
"""

import hashlib
import ipaddress
import secrets
import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, WebSocket, status
from pymongo.database import Database

from .db import get_db, utcnow
from .projects import ProjectStatus

SECRET, PUBLISHABLE = "secret", "publishable"
PREFIXES = {SECRET: "hdp_sk_", PUBLISHABLE: "hdp_pk_"}
SHOWN_CHARACTERS = 12          # stored and shown, so keys can be told apart
LAST_USED_EVERY = timedelta(minutes=1)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_key(db: Database, project_id: str, kind: str, name: str, created_by: str | None) -> tuple[dict[str, Any], str]:
    """Store a new key and return it with its plain text, which is never available again."""
    if kind not in PREFIXES:
        raise ValueError(f"kind must be {SECRET} or {PUBLISHABLE}")
    plain = PREFIXES[kind] + secrets.token_urlsafe(32)
    doc = {
        "_id": "key_" + uuid.uuid4().hex[:16],
        "project_id": project_id,
        "kind": kind,
        "name": name.strip(),
        "prefix": plain[:SHOWN_CHARACTERS],
        "key_hash": hash_key(plain),
        "created_at": utcnow(),
        "created_by": created_by,
        "last_used_at": None,
        "revoked_at": None,
    }
    db.api_keys.insert_one(doc)
    return doc, plain


def _ip_allowed(project: dict[str, Any], host: str | None) -> bool:
    allowed = project.get("allowed_ips") or []
    if not allowed:
        return True
    if host is None:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(entry, strict=False) for entry in allowed)


def authenticate(db: Database, authorization: str | None, host: str | None, kinds: tuple[str, ...]) -> dict[str, Any]:
    """The project a key belongs to, or an HTTPException saying why not."""
    scheme, _, key = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not key.startswith(tuple(PREFIXES.values())):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Send the project's API key: Authorization: Bearer hdp_...")
    doc = db.api_keys.find_one({"key_hash": hash_key(key.strip())})
    if doc is None or doc.get("revoked_at") is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown or revoked API key")
    if doc["kind"] not in kinds:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This needs the project's secret key (hdp_sk_...)")
    project = db.projects.find_one({"_id": doc["project_id"]})
    if project is None or project["status"] != ProjectStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This project is not active")
    if not _ip_allowed(project, host):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requests from {host} are not allowed for this project")
    now = utcnow()
    if doc.get("last_used_at") is None or now - doc["last_used_at"] > LAST_USED_EVERY:
        db.api_keys.update_one({"_id": doc["_id"]}, {"$set": {"last_used_at": now}})
    return project


def _host(request: Request | WebSocket) -> str | None:
    return request.client.host if request.client else None


def any_key(request: Request, db: Annotated[Database, Depends(get_db)]) -> dict[str, Any]:
    return authenticate(db, request.headers.get("authorization"), _host(request), (SECRET, PUBLISHABLE))


def secret_key(request: Request, db: Annotated[Database, Depends(get_db)]) -> dict[str, Any]:
    return authenticate(db, request.headers.get("authorization"), _host(request), (SECRET,))


AnyKeyProject = Annotated[dict[str, Any], Depends(any_key)]
SecretKeyProject = Annotated[dict[str, Any], Depends(secret_key)]
