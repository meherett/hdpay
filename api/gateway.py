"""
The gateway API, for a project's backend (and, with a publishable key, its checkout page).

Every route needs the project's API key: Authorization: Bearer hdp_sk_... (or hdp_pk_... where noted).

    GET  /v1/chains                 networks and assets the project accepts           secret or publishable
    GET  /v1/products               the project's products                            secret or publishable
    POST /v1/deposits               open a deposit (or get the account's open one)    secret or publishable
    POST /v1/deposits/{id}/cancel   cancel a pending deposit                          secret
    GET  /v1/deposits/{id}          one of the project's deposits                     secret or publishable
    GET  /v1/deposits               the project's deposits, newest first              secret
    GET  /v1/events?after=<seq>     events after a sequence (default: the last acked)  secret
    POST /v1/events/ack             {"seq": n}: everything up to n is handled           secret
    WS   /v1/events/stream          events as they happen; see below                    secret

The stream sends {"type": "ready", "after": n}, then every event after n, then each new one as it
happens. Send {"ack": seq} once an event is handled; a reconnect resumes after the last ack, or after
?after=<seq> if given. An event can arrive twice after a reconnect: ignore ids already handled.
"""

import asyncio
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pymongo import DESCENDING
from pymongo.database import Database

from . import events
from .apikeys import SECRET, AnyKeyProject, SecretKeyProject, authenticate
from .db import Deposit, Status, get_db
from .deposits import cancel_deposit, get_deposit_or_404, open_deposit
from .projects import USD_STABLECOINS, accepted, get_project
from .schemas import (
    Acknowledge,
    AssetOut,
    ChainOut,
    CreateDeposit,
    EventList,
    EventOut,
    ProductOut,
    ProjectDepositOut,
)

router = APIRouter(prefix="/v1", tags=["gateway"])
DB = Annotated[Database, Depends(get_db)]
STREAM_POLL_SECONDS = 1.0


@router.get("/chains", response_model=list[ChainOut])
def project_chains(project: AnyKeyProject):
    """The networks and assets this project accepts."""
    return [
        ChainOut(
            key=chain.key,
            name=chain.name,
            family=chain.family,
            confirmations=chain.confirmations,
            assets=[
                AssetOut(
                    symbol=a,
                    decimals=chain.decimals(a),
                    contract=None if chain.is_native(a) else chain.tokens[a].contract,
                    standard=None if chain.is_native(a) else chain.token_standard,
                    usd=a in USD_STABLECOINS,
                )
                for a in assets
            ],
        )
        for chain, assets in accepted(project)
    ]


@router.get("/products", response_model=list[ProductOut])
def project_products(project: AnyKeyProject):
    return [
        ProductOut(value=p["value"], name=p["name"], subname=p.get("subname"), price=p.get("price"))
        for p in project.get("products") or []
    ]


@router.post("/deposits", response_model=ProjectDepositOut)
def create_deposit(body: CreateDeposit, project: AnyKeyProject, db: DB):
    """Opens a deposit for account_id, trusting it as given: only the project's backend, or a checkout
    page it serves, should hold a key."""
    return ProjectDepositOut.from_deposit(open_deposit(db, project, body), project)


@router.post("/deposits/{deposit_id}/cancel", response_model=ProjectDepositOut)
def cancel(deposit_id: str, project: SecretKeyProject, db: DB):
    """Cancel a pending deposit, with nothing received: it becomes cancelled, and the project gets
    deposit.cancelled. 409 for any other status. The address stays watched: a payment already sent is
    still counted. Secret key only: a publishable key is in a browser."""
    return ProjectDepositOut.from_deposit(cancel_deposit(db, project, deposit_id), project)


@router.get("/deposits/{deposit_id}", response_model=ProjectDepositOut)
def get_deposit(deposit_id: str, project: AnyKeyProject, db: DB):
    return ProjectDepositOut.from_deposit(get_deposit_or_404(db, deposit_id, project["_id"]), project)


@router.get("/deposits", response_model=list[ProjectDepositOut])
def list_deposits(
    project: SecretKeyProject,
    db: DB,
    account_id: str | None = None,
    status: Status | None = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query: dict[str, Any] = {"project_id": project["_id"]}
    if account_id is not None:
        query["account_id"] = account_id
    if status is not None:
        query["status"] = status.value
    cursor = db.deposits.find(query).sort([("created_at", DESCENDING), ("_id", DESCENDING)]).skip(skip).limit(limit)
    return [ProjectDepositOut.from_deposit(Deposit(doc), project) for doc in cursor]


@router.get("/events", response_model=EventList)
def list_events(
    project: SecretKeyProject,
    db: DB,
    after: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    """Events after a sequence number, oldest first; by default after the last one acknowledged."""
    start = project.get("events_acked", 0) if after is None else after
    found = events.after(db, project["_id"], start, limit)
    return EventList(
        events=[EventOut(**events.public(e)) for e in found],
        last_seq=found[-1]["seq"] if found else start,
    )


@router.post("/events/ack", status_code=204)
def acknowledge(body: Acknowledge, project: SecretKeyProject, db: DB):
    events.acknowledge(db, project["_id"], body.seq)


@router.websocket("/events/stream")
async def stream(websocket: WebSocket, after: Annotated[int | None, Query(ge=0)] = None):
    db = get_db()
    host = websocket.client.host if websocket.client else None
    try:
        project = await asyncio.to_thread(authenticate, db, websocket.headers.get("authorization"), host, (SECRET,))
    except HTTPException as exc:
        # 4401 / 4403: the HTTP reason, in WebSocket's application range
        await websocket.close(code=4000 + exc.status_code, reason=str(exc.detail)[:120])
        return

    await websocket.accept()
    seq = project.get("events_acked", 0) if after is None else after
    await websocket.send_json({"type": "ready", "after": seq})

    async def receive_acks() -> None:
        while True:
            message = await websocket.receive_text()
            try:
                ack = int(json.loads(message)["ack"])
            except (ValueError, KeyError, TypeError):
                await websocket.send_json({"type": "error", "message": 'send {"ack": <seq>}'})
                continue
            await asyncio.to_thread(events.acknowledge, db, project["_id"], ack)

    async def send_events() -> None:
        nonlocal seq
        while True:
            # a project disabled or deleted while connected loses the stream
            current = await asyncio.to_thread(get_project, db, project["_id"])
            if current is None or current["status"] != project["status"]:
                await websocket.close(code=4403, reason="This project is not active")
                return
            found = await asyncio.to_thread(events.after, db, project["_id"], seq, 100)
            for event in found:
                await websocket.send_json(events.public(event))
                seq = event["seq"]
            if len(found) < 100:
                await asyncio.sleep(STREAM_POLL_SECONDS)

    tasks = [asyncio.create_task(receive_acks()), asyncio.create_task(send_events())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                pass
