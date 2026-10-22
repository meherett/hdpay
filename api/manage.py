"""
Server-side user management, for when no admin can sign in:

    python -m api.manage create-admin --email you@example.com --name "Your Name" [--owner]
    python -m api.manage set-password --email you@example.com
    python -m api.manage set-role --email you@example.com --role owner
    python -m api.manage migrate-project-ids      projects with an old "prj_..." id get a MongoDB id
    python -m api.manage restore-discounts        unpaid deposits moved to the full price get their discount back
    python -m api.manage users

Passwords are read from the terminal (or HDPAY_NEW_PASSWORD, for scripts), never from arguments.
"""

import argparse
import getpass
import os
import sys

from pymongo.errors import DuplicateKeyError

from .auth import ROLES, new_user, set_password
from .db import create_indexes, get_db


def _password() -> str:
    password = os.environ.get("HDPAY_NEW_PASSWORD")
    if password is None:
        password = getpass.getpass("Password (8 to 72 characters): ")
        if getpass.getpass("Again: ") != password:
            sys.exit("The passwords differ")
    if not 8 <= len(password.encode()) <= 72:
        sys.exit("A password must be 8 to 72 characters")
    return password


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m api.manage", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin", help="add an admin user")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--owner", action="store_true", help="an owner, who can also delete")
    role = commands.add_parser("set-role", help="make a user an owner, admin or viewer")
    role.add_argument("--email", required=True)
    role.add_argument("--role", required=True, choices=ROLES)
    reset = commands.add_parser("set-password", help="set a user's password; their sessions end")
    reset.add_argument("--email", required=True)
    commands.add_parser("users", help="list users")
    commands.add_parser("migrate-project-ids", help='give projects with an old "prj_..." id a MongoDB id')
    commands.add_parser("restore-discounts", help="unpaid deposits moved to the full price get their discount back")
    args = parser.parse_args()

    db = get_db()
    create_indexes(db)
    if args.command == "create-admin":
        try:
            user = new_user(db, args.email, args.name, _password(), "owner" if args.owner else "admin")
        except DuplicateKeyError:
            sys.exit(f"{args.email} already exists; use set-password")
        print(f"{user['role']} {user['email']} created")
    elif args.command == "set-role":
        email = args.email.strip().lower()
        user = db.users.find_one({"email": email})
        if user is None:
            sys.exit(f"no user {args.email}")
        others = db.users.count_documents({"role": "owner", "is_active": True, "_id": {"$ne": user["_id"]}})
        if user["role"] == "owner" and args.role != "owner" and others == 0:
            sys.exit("this is the last owner: make someone else an owner first")
        db.users.update_one({"_id": user["_id"]}, {"$set": {"role": args.role}})
        print(f"{email} is {args.role} now")
    elif args.command == "migrate-project-ids":
        from .projects import migrate_project_ids

        moved = migrate_project_ids(db)
        for old, new in moved:
            print(f"{old} -> {new}")
        print(f"{len(moved)} project(s) moved" if moved else "every project already has a MongoDB id")
    elif args.command == "restore-discounts":
        from .discounts import restore_unpaid

        print(f"{restore_unpaid(db)} deposit(s) back on their discounted amount")
    elif args.command == "set-password":
        user = db.users.find_one({"email": args.email.strip().lower()})
        if user is None:
            sys.exit(f"no user {args.email}")
        set_password(db, user["_id"], _password())
        print(f"password set for {user['email']}; their sessions ended")
    else:
        for user in db.users.find().sort("created_at", 1):
            state = "active" if user["is_active"] else "disabled"
            print(f"{user['email']:40} {user['role']:7} {state:8} {user['name']}")


if __name__ == "__main__":
    main()
