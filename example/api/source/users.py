"""
The shop's users: account ID, name, email and password. Kept simple on purpose.

    python -m source.users seed                 some sample users, each with a new password (printed once)
    python -m source.users add <email> --name "Ada Lovelace" [--account-id 1006]
                                                one user, with a new password (printed once)
    python -m source.users list
    python -m source.users password <email>     sets a new password (asks for it)

The account ID is the one HDPay derives the user's deposit addresses from, so it must be a whole
number from 0 to 2147483647 and never change.
"""

import argparse
import getpass
import hashlib
import hmac
import secrets
import sys

from .config import Settings
from .store import Store

MAX_ACCOUNT_ID = 2**31 - 1
SAMPLE_USERS = [
    ("1001", "Ada Lovelace", "ada@example.com"),
    ("1002", "Alan Turing", "alan@example.com"),
    ("1003", "Grace Hopper", "grace@example.com"),
    ("1004", "Katherine Johnson", "katherine@example.com"),
    ("1005", "Satoshi Nakamoto", "satoshi@example.com"),
]

# scrypt, from the standard library: n=2^14, r=8, p=1
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=_N, r=_R, p=_P, dklen=32)
    return hmac.compare_digest(candidate.hex(), digest)


def public(user: dict) -> dict:
    """A user as the web sees it: never the password hash."""
    return {"account_id": user["account_id"], "name": user["name"], "email": user["email"]}


def seed(store: Store) -> list[tuple[str, str, str, str]]:
    """Adds the sample users that are not there yet; returns them with their new passwords."""
    added = []
    for account_id, name, email in SAMPLE_USERS:
        password = secrets.token_urlsafe(9)
        if store.add_user(account_id, name, email, hash_password(password)):
            added.append((account_id, name, email, password))
    return added


def add(store: Store, email: str, name: str, account_id: str | None = None) -> tuple[str, str]:
    """One new user, under the next free account ID unless one is given; returns it and the password."""
    if account_id is None:
        taken = [int(u["account_id"]) for u in store.users()]
        account_id = str(max(taken, default=1000) + 1)
    if not account_id.isdigit() or int(account_id) > MAX_ACCOUNT_ID:
        raise ValueError(f"the account ID must be a whole number from 0 to {MAX_ACCOUNT_ID}")
    if store.user_by_email(email) is not None:
        raise ValueError(f"{email} already has an account")
    password = secrets.token_urlsafe(9)
    if not store.add_user(account_id, name, email, hash_password(password)):
        raise ValueError(f"account ID {account_id} is taken")
    return account_id, password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m source.users", description="The example shop's users")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="add the sample users, with new passwords")
    one = commands.add_parser("add", help="add one user, with a new password")
    one.add_argument("email")
    one.add_argument("--name", required=True)
    one.add_argument("--account-id", help="defaults to the next free one")
    commands.add_parser("list", help="list the users")
    password = commands.add_parser("password", help="set a user's password")
    password.add_argument("email")
    args = parser.parse_args(argv)

    store = Store(Settings().database)
    if args.command == "seed":
        added = seed(store)
        if not added:
            print("The sample users are already there. Set a password with: python -m source.users password <email>")
        for account_id, name, email, secret in added:
            print(f"{account_id:>6}  {name:<20} {email:<24} password: {secret}")
        if added:
            print("\nThe passwords are shown only now.")
    elif args.command == "add":
        try:
            account_id, secret = add(store, args.email.strip(), " ".join(args.name.split()), args.account_id)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"{account_id:>6}  {args.name:<20} {args.email:<24} password: {secret}")
        print("\nThe password is shown only now.")
    elif args.command == "list":
        for user in store.users():
            print(f"{user['account_id']:>6}  {user['name']:<20} {user['email']}")
    else:
        user = store.user_by_email(args.email)
        if user is None:
            print(f"No user with the email {args.email}", file=sys.stderr)
            return 1
        secret = getpass.getpass("New password: ")
        if len(secret) < 8:
            print("Use at least 8 characters", file=sys.stderr)
            return 1
        store.set_password(user["account_id"], hash_password(secret))
        print(f"Password set for {user['email']}; their sessions are signed out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
