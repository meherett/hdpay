"""
The shop's own records, in SQLite: its users and their sign-in sessions, which email each deposit
belongs to, and the HDPay events already handled (delivery is at least once, so an event can arrive twice).
"""

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    account_id    TEXT PRIMARY KEY,   -- HDPay's account ID: a level of the deposit's derivation path
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES users (account_id),
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    deposit_id TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    account_id TEXT NOT NULL,
    product    TEXT,
    created_at TEXT NOT NULL,
    paid_at    TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id         TEXT PRIMARY KEY,
    handled_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock, self.db:
            self.db.executescript(SCHEMA)

    def add_order(self, deposit_id: str, email: str, account_id: str, product: str | None) -> bool:
        """Records the order; False when the deposit already has one (HDPay gave back an open deposit)."""
        with self.lock, self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO orders (deposit_id, email, account_id, product, created_at) VALUES (?, ?, ?, ?, ?)",
                (deposit_id, email, account_id, product, _now()),
            )
            return cursor.rowcount == 1

    def order(self, deposit_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM orders WHERE deposit_id = ?", (deposit_id,)).fetchone()
        return dict(row) if row else None

    def mark_paid(self, deposit_id: str) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE orders SET paid_at = ? WHERE deposit_id = ? AND paid_at IS NULL", (_now(), deposit_id))

    def handled(self, event_id: str) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone() is not None

    def remember(self, event_id: str) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO events (id, handled_at) VALUES (?, ?)", (event_id, _now()))

    # users

    def add_user(self, account_id: str, name: str, email: str, password_hash: str) -> bool:
        """False when the account ID or email is taken."""
        with self.lock, self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO users (account_id, name, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (account_id, name, email, password_hash, _now()),
            )
            return cursor.rowcount == 1

    def user(self, account_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM users WHERE account_id = ?", (account_id,)).fetchone()
        return dict(row) if row else None

    def user_by_email(self, email: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None

    def users(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM users ORDER BY CAST(account_id AS INTEGER)").fetchall()
        return [dict(row) for row in rows]

    def set_password(self, account_id: str, password_hash: str) -> bool:
        with self.lock, self.db:
            cursor = self.db.execute("UPDATE users SET password_hash = ? WHERE account_id = ?", (password_hash, account_id))
            self.db.execute("DELETE FROM sessions WHERE account_id = ?", (account_id,))
            return cursor.rowcount == 1

    # sessions

    def add_session(self, token_hash: str, account_id: str, days: int) -> None:
        expires = (datetime.now(UTC) + timedelta(days=days)).isoformat(timespec="seconds")
        with self.lock, self.db:
            self.db.execute("INSERT INTO sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)", (token_hash, account_id, expires))

    def session_user(self, token_hash: str) -> dict[str, Any] | None:
        """The signed-in user, while the session lasts."""
        with self.lock:
            row = self.db.execute(
                "SELECT users.* FROM sessions JOIN users USING (account_id) WHERE token_hash = ? AND expires_at > ?",
                (token_hash, _now()),
            ).fetchone()
        return dict(row) if row else None

    def end_session(self, token_hash: str) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
