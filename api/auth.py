"""
Admin sign-in: users with an email and a password, and three roles.

    owner    everything an admin does, and: users, the sweeper page, deleting deposit addresses and
             projects. There is always at least one.
    admin    projects, API keys and support actions on deposits
    viewer   reads everything else, changes nothing

Signing in returns a short-lived access token (a JWT, HDPAY_ACCESS_TOKEN_MINUTES) and a refresh token
(random, HDPAY_REFRESH_TOKEN_DAYS). Only a hash of the refresh token is stored, and each refresh replaces
it, so a stolen one stops working after its first use. Changing a password ends every session.

An owner sees every project. An admin or a viewer sees the projects an owner gave them: all of them
(projects = None, the default) or a list of project ids; anything else answers 404, as if it did not
exist. The scope is read from the database on every request, so a change applies at once.

There is no email: an owner sets other users' passwords, and `python -m api.manage` creates the
first owner or resets a password from the server.
"""

import calendar
import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Annotated, Any

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pymongo import ReturnDocument
from pymongo.database import Database

from .config import settings
from .db import get_db, utcnow
from .schemas import ChangePassword, Login, Refresh, TokenPair, UserOut

ROLES = ("owner", "admin", "viewer")
# who may change things: an owner can do whatever an admin can
CHANGES = ("owner", "admin")
_bearer = HTTPBearer(auto_error=False)
# compared against when the email is unknown, so a reply takes as long either way
_DUMMY_HASH = bcrypt.hashpw(b"not a real password", bcrypt.gensalt())


# ---- passwords and tokens

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, password_hash: str | None) -> bool:
    return bcrypt.checkpw(password.encode(), (password_hash or _DUMMY_HASH.decode()).encode()) and password_hash is not None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _secret() -> str:
    if not settings.jwt_secret:
        raise HTTPException(503, "Admin sign-in is off until HDPAY_JWT_SECRET is set")
    return settings.jwt_secret


def access_token(user: dict[str, Any]) -> str:
    now = utcnow()
    payload = {
        "sub": user["_id"],
        "role": user["role"],
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "jti": secrets.token_urlsafe(8),
    }
    return jwt.encode(payload, _secret(), algorithm=settings.jwt_algorithm)


def issue_tokens(db: Database, user: dict[str, Any], request: Request | None) -> TokenPair:
    refresh = secrets.token_urlsafe(32)
    now = utcnow()
    db.sessions.insert_one({
        "_id": "ses_" + uuid.uuid4().hex,
        "user_id": user["_id"],
        "token_hash": hash_token(refresh),
        "user_agent": request.headers.get("user-agent", "")[:300] if request else "",
        "ip": request.client.host if request and request.client else None,
        "created_at": now,
        "expires_at": now + timedelta(days=settings.refresh_token_days),
    })
    return TokenPair(
        access_token=access_token(user),
        refresh_token=refresh,
        expires_in=settings.access_token_minutes * 60,
        user=UserOut.from_document(user),
    )


# ---- users

def new_user(
    db: Database, email: str, name: str, password: str, role: str, projects: list[str] | None = None
) -> dict[str, Any]:
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    now = utcnow()
    doc = {
        "_id": "usr_" + uuid.uuid4().hex[:16],
        "email": email.strip().lower(),
        "name": name.strip(),
        "password_hash": hash_password(password),
        "role": role,
        "projects": None if role == "owner" else projects,   # None: every project
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "last_login_at": None,
        "password_changed_at": now,
    }
    db.users.insert_one(doc)
    return doc


def set_password(db: Database, user_id: str, password: str) -> dict[str, Any] | None:
    """New password; every session of the user ends, and access tokens issued before stop working."""
    now = utcnow()
    user = db.users.find_one_and_update(
        {"_id": user_id},
        {"$set": {"password_hash": hash_password(password), "password_changed_at": now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    db.sessions.delete_many({"user_id": user_id})
    return user


# ---- who is calling

DB = Annotated[Database, Depends(get_db)]


def current_user(db: DB, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> dict[str, Any]:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again", headers={"WWW-Authenticate": "Bearer"})
    if credentials is None:
        raise unauthorized
    try:
        claims = jwt.decode(credentials.credentials, _secret(), algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        raise unauthorized from None
    user = db.users.find_one({"_id": claims.get("sub")})
    if user is None:
        raise unauthorized
    # a token issued before the password changed no longer counts
    changed = user.get("password_changed_at")
    if changed is not None and claims.get("iat", 0) < calendar.timegm(changed.utctimetuple()) - 1:
        raise unauthorized
    if not user.get("is_active"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled")
    return user


CurrentUser = Annotated[dict[str, Any], Depends(current_user)]


def require_admin(user: CurrentUser) -> dict[str, Any]:
    """Changes are for admins and owners; viewers can only read. The role is read from the database, not the token."""
    if user["role"] not in CHANGES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can do this")
    return user


def require_owner(user: CurrentUser) -> dict[str, Any]:
    """Deleting is for owners."""
    if user["role"] != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an owner can do this")
    return user


def project_scope(user: dict[str, Any]) -> list[str] | None:
    """The projects a user may see and change: None for all of them (owners, and anyone given all)."""
    return None if user["role"] == "owner" else user.get("projects")


def can_see(user: dict[str, Any], project_id: str) -> bool:
    scope = project_scope(user)
    return scope is None or project_id in scope


AdminUser = Annotated[dict[str, Any], Depends(require_admin)]
OwnerUser = Annotated[dict[str, Any], Depends(require_owner)]


# ---- wrong-password limit

def _attempts_key(request: Request, email: str) -> str:
    ip = request.client.host if request.client else "unknown"
    return f"login:{ip}:{email}"


def _check_attempts(db: Database, key: str) -> None:
    doc = db.login_attempts.find_one({"_id": key})
    if doc and doc["count"] >= settings.max_login_attempts:
        wait = max(int((doc["expires_at"] - utcnow()).total_seconds()), 1)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many attempts. Try again in {max(wait // 60, 1)} minute(s).",
            headers={"Retry-After": str(wait)},
        )


def _count_failure(db: Database, key: str) -> None:
    db.login_attempts.update_one(
        {"_id": key},
        {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": utcnow() + timedelta(minutes=settings.login_window_minutes)}},
        upsert=True,
    )


# ---- routes

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPair)
def login(body: Login, request: Request, db: DB):
    email = body.email.strip().lower()
    key = _attempts_key(request, email)
    _check_attempts(db, key)
    user = db.users.find_one({"email": email})
    if not check_password(body.password, user and user.get("password_hash")):
        _count_failure(db, key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    if not user.get("is_active"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled")
    db.login_attempts.delete_one({"_id": key})
    db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login_at": utcnow()}})
    return issue_tokens(db, user, request)


@router.post("/refresh", response_model=TokenPair)
def refresh(body: Refresh, request: Request, db: DB):
    # the old session goes away as the new one is issued: a refresh token works once
    session = db.sessions.find_one_and_delete({"token_hash": hash_token(body.refresh_token)})
    if session is None or session["expires_at"] < utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again")
    user = db.users.find_one({"_id": session["user_id"]})
    if user is None or not user.get("is_active"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again")
    return issue_tokens(db, user, request)


@router.post("/logout", status_code=204)
def logout(body: Refresh, db: DB):
    db.sessions.delete_one({"token_hash": hash_token(body.refresh_token)})


@router.post("/logout-all", status_code=204)
def logout_all(user: CurrentUser, db: DB):
    db.sessions.delete_many({"user_id": user["_id"]})


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return UserOut.from_document(user)


@router.post("/change-password", status_code=204)
def change_password(body: ChangePassword, user: CurrentUser, db: DB):
    if not check_password(body.old_password, user.get("password_hash")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The current password is wrong")
    set_password(db, user["_id"], body.new_password)
