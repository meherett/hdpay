"""
The HDPay API: one process serving

    /auth/...         admin sign-in (users)
    /admin/...        the admin app: users, projects, deposits, operations
    /v1/...           the gateway, for projects (API key)
    /deposits/{id}    a deposit's public view, for checkout pages: its random id is the permission
"""

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pymongo.database import Database

from . import admin, auth, gateway
from .chains import CHAINS
from .config import settings
from .db import create_indexes, get_db
from .deposits import get_deposit_or_404
from .projects import get_project
from .schemas import DepositOut


@asynccontextmanager
async def lifespan(_: FastAPI):
    # so chains file reloads (api.chains) show next to uvicorn's own lines
    log = logging.getLogger("hdpay")
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    if not settings.jwt_secret:
        log.warning("HDPAY_JWT_SECRET is not set: admin sign-in is off")
    db = get_db()
    db.client.admin.command("ping")
    create_indexes(db)
    yield


app = FastAPI(title="hdpay", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(gateway.router)
DB = Annotated[Database, Depends(get_db)]


@app.middleware("http")
async def follow_chains_file(request: Request, call_next):
    # an edit to the chains file shows up in the web and admin apps without a restart
    CHAINS.refresh()
    return await call_next(request)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/deposits/{deposit_id}", response_model=DepositOut)
def public_deposit(deposit_id: str, db: DB):
    """The checkout page's view. The id is a random UUID, so it works as a capability link."""
    deposit = get_deposit_or_404(db, deposit_id)
    return DepositOut.from_deposit(deposit, get_project(db, deposit.project_id))


def main() -> None:
    from pathlib import Path

    import uvicorn

    # with HDPAY_RELOAD only the package is watched, not .venv or the web apps
    package = str(Path(__file__).resolve().parent)
    uvicorn.run(
        "api.main:app", host="0.0.0.0", port=settings.port, reload=settings.reload, reload_dirs=[package],
        proxy_headers=True, forwarded_allow_ips=settings.forwarded_allow_ips,
    )


if __name__ == "__main__":
    main()
