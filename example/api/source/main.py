"""
The example shop's API, for its web page. It holds the HDPay secret key; the page never talks to HDPay.

    GET  /users                  the shop's users, to pick who is buying (a sample: a real shop signs them in)
    GET  /products               what can be bought: HDPay's project products that have a price
    GET  /chains                 networks and assets accepted
    POST /orders                 {account_id, product, chain, asset, name?, email?, discount_percent?, discount_ends_at?}:
                                 account_id is one of the shop's users, or, with name and email, a custom buyer
                                 a payment address, at the product's price less the discount
    GET  /orders/{id}            the order's payment, for the payment page
    POST /orders/{id}/renew      a fresh address after the old one expired
    POST /orders/{id}/cancel     the customer gives up on a pending address, to pay another way
    GET  /orders/unfinished?account_id=…  the account's short payments whose time for the rest ran out
    POST /orders/{id}/reopen     {product?}: more time for the rest of one of them, or for another product
    POST /auth/login             {email, password}: a session token and the user
    GET  /auth/me                the signed-in user (Authorization: Bearer <token>)
    POST /auth/logout            ends the session

Meanwhile it follows HDPay's events and emails the customer as their payment moves along.
"""

import asyncio
import contextlib
import hashlib
import logging
import re
import secrets
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import Settings
from .events import follow
from .hdpay import HDPay, HDPayError
from .mailer import Mailer
from .shop import Shop, public
from .store import Store
from .users import public as public_user
from .users import verify_password

log = logging.getLogger("example")

SESSION_DAYS = 7
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class Login(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _bearer(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Sign in first")
    return token


class Reopen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product: str | None = Field(default=None, max_length=120)   # another product; left out, the same one


class Order(BaseModel):
    # an unknown field is refused, so a page and an API out of step fail loudly instead of dropping it
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(max_length=10)   # one of the shop's users, or a custom buyer's
    # a custom buyer, for this order only: not one of the shop's users, and not stored as one
    name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    product: str = Field(max_length=120)
    chain: str = Field(max_length=60)
    asset: str = Field(max_length=20)
    # a discount for this customer: a percent off until a moment; HDPay keeps it with the address
    discount_percent: int | None = Field(default=None, ge=1, le=99)
    discount_ends_at: datetime | None = None

    @model_validator(mode="after")
    def _discount(self) -> "Order":
        if (self.discount_percent is None) != (self.discount_ends_at is None):
            raise ValueError("A discount needs both a percent and an end")
        return self

    @model_validator(mode="after")
    def _custom_buyer(self) -> "Order":
        self.name = (self.name or "").strip() or None
        self.email = (self.email or "").strip() or None
        if (self.name is None) != (self.email is None):
            raise ValueError("A custom buyer needs both a name and an email")
        if self.email is not None and not EMAIL.fullmatch(self.email):
            raise ValueError("Enter an email like name@example.com")
        if not self.account_id.isdigit():
            raise ValueError("The account ID must be a whole number")
        return self

    def buyer(self) -> dict[str, str] | None:
        """The custom buyer, or None for one of the shop's users."""
        return {"account_id": self.account_id, "name": self.name, "email": self.email} if self.email else None


def create_app(settings: Settings | None = None, hdpay: HDPay | None = None, follow_events: bool = True) -> FastAPI:
    settings = settings or Settings()
    shop = Shop(
        settings,
        hdpay or HDPay(settings.hdpay_url, settings.hdpay_secret_key),
        Store(settings.database),
        Mailer(settings),
    )

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = None
        if follow_events and settings.hdpay_secret_key:
            task = asyncio.create_task(follow(shop))
        elif follow_events:
            log.warning("HDPAY_SECRET_KEY is empty: no orders and no emails until it is set")
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title=f"{settings.shop_name} API", lifespan=lifespan)
    app.state.shop = shop
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.exception_handler(HDPayError)
    async def hdpay_error(_: Request, exc: HDPayError) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    def current_user(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        user = shop.store.session_user(_token_hash(_bearer(authorization)))
        if user is None:
            raise HTTPException(401, "Your session ended: sign in again")
        return user

    @app.post("/auth/login")
    def login(body: Login) -> dict[str, Any]:
        user = shop.store.user_by_email(body.email.strip())
        if user is None or not verify_password(body.password, user["password_hash"]):
            raise HTTPException(401, "Wrong email or password")
        token = secrets.token_urlsafe(32)
        shop.store.add_session(_token_hash(token), user["account_id"], SESSION_DAYS)
        return {"token": token, "user": public_user(user)}

    @app.get("/auth/me")
    def me(user: Annotated[dict[str, Any], Depends(current_user)]) -> dict[str, Any]:
        return public_user(user)

    @app.post("/auth/logout", status_code=204)
    def logout(authorization: Annotated[str | None, Header()] = None) -> None:
        shop.store.end_session(_token_hash(_bearer(authorization)))

    @app.get("/users")
    def users() -> list[dict[str, Any]]:
        return [public_user(u) for u in shop.store.users()]

    @app.get("/products")
    def products() -> list[dict[str, Any]]:
        return shop.products()

    @app.get("/chains")
    def chains() -> list[dict[str, Any]]:
        return shop.hdpay.chains()

    @app.post("/orders")
    def place(order: Order, background: BackgroundTasks) -> dict[str, Any]:
        discount = None
        if order.discount_percent:
            discount = {"percent": order.discount_percent, "ends_at": order.discount_ends_at.isoformat()}
        user, deposit, new = shop.buy(order.account_id, order.product, order.chain, order.asset, discount, order.buyer())
        if new:
            background.add_task(shop.send_address, user["email"], deposit)
        return public(deposit)

    # before /orders/{deposit_id}, which would take "unfinished" for an order id
    @app.get("/orders/unfinished")
    def unfinished(account_id: str) -> list[dict[str, Any]]:
        return [public(d) for d in shop.unfinished(account_id)]

    @app.get("/orders/{deposit_id}")
    def find(deposit_id: str) -> dict[str, Any]:
        return public(shop.find(deposit_id))

    @app.post("/orders/{deposit_id}/renew")
    def renew(deposit_id: str, background: BackgroundTasks) -> dict[str, Any]:
        deposit, new = shop.renew(deposit_id)
        if new:
            email = shop.store.order(deposit["id"])["email"]
            background.add_task(shop.send_address, email, deposit)
        return public(deposit)

    @app.post("/orders/{deposit_id}/reopen")
    def reopen(deposit_id: str, body: Reopen | None = None) -> dict[str, Any]:
        return public(shop.reopen(deposit_id, body.product if body else None))

    @app.post("/orders/{deposit_id}/cancel")
    def cancel(deposit_id: str) -> dict[str, Any]:
        return public(shop.cancel(deposit_id))

    return app
