from __future__ import annotations

import getpass
import hmac
import os
import re
import secrets
import sqlite3
import sys
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from pathlib import Path
from threading import Lock
from typing import Any

from flask import Flask, g, jsonify, request, send_from_directory, session
import psycopg
from psycopg.rows import dict_row
from werkzeug.security import check_password_hash, generate_password_hash
from waitress import serve

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
PREVIEW_MODE = os.environ.get("INVESTIT_PREVIEW_MODE", "0") == "1"
DATABASE = Path(os.environ.get("INVESTIT_DATABASE", INSTANCE_DIR / "investit.sqlite3"))
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
if not DATABASE_URL:
    DATABASE.parent.mkdir(parents=True, exist_ok=True)

DATABASE_ERRORS = (sqlite3.Error, psycopg.Error)
DATABASE_INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg.errors.UniqueViolation)

PRODUCTS = [
    {"id": "electrical-gadgets", "name": "Electrical gadgets", "price_pesewas": 8000, "daily_pesewas": 500, "duration_days": 30},
    {"id": "assets", "name": "Assets", "price_pesewas": 12000, "daily_pesewas": 800, "duration_days": 30},
    {"id": "stock", "name": "Stock", "price_pesewas": 16000, "daily_pesewas": 1200, "duration_days": 30},
    {"id": "mobile-gadgets", "name": "Mobile gadgets", "price_pesewas": 19000, "daily_pesewas": 1500, "duration_days": 30},
    {"id": "social-media-monetization", "name": "Social media monetization media", "price_pesewas": 23000, "daily_pesewas": 7000, "duration_days": 30},
]
PROVIDER_NAMES = {"mtn": "MTN Mobile Money", "telecel": "Telecel Cash", "at": "AT Money"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'investor')),
    balance_pesewas INTEGER NOT NULL DEFAULT 0 CHECK (balance_pesewas >= 0),
    banned INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    type TEXT NOT NULL,
    amount_pesewas INTEGER NOT NULL CHECK (amount_pesewas > 0),
    status TEXT NOT NULL,
    description TEXT NOT NULL,
    reference TEXT,
    provider TEXT,
    payout_phone TEXT,
    admin_id TEXT REFERENCES users(id),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS transactions_user_time ON transactions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS transactions_status_type ON transactions(status, type);
CREATE TABLE IF NOT EXISTS investments (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    product_id TEXT NOT NULL,
    product_name TEXT NOT NULL,
    price_pesewas INTEGER NOT NULL,
    daily_pesewas INTEGER NOT NULL,
    duration_days INTEGER NOT NULL,
    started_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS dividend_payouts (
    investment_id TEXT NOT NULL REFERENCES investments(id),
    day INTEGER NOT NULL,
    transaction_id TEXT NOT NULL UNIQUE REFERENCES transactions(id),
    created_at INTEGER NOT NULL,
    PRIMARY KEY (investment_id, day)
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    admin_id TEXT NOT NULL REFERENCES users(id),
    message TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    csrf_token TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
"""

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("INVESTIT_SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("INVESTIT_HTTPS", "0") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    MAX_CONTENT_LENGTH=32 * 1024,
)
_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_login_lock = Lock()


class DatabaseConnection:
    def __init__(self, connection: Any, *, postgres: bool):
        self.raw = connection
        self.postgres = postgres

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()):
        if self.postgres:
            sql = sql.replace("?", "%s")
        return self.raw.execute(sql, parameters)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)

    @contextmanager
    def transaction(self):
        if self.postgres:
            with self.raw.transaction():
                yield self
            return
        self.execute("BEGIN IMMEDIATE")
        try:
            yield self
            self.raw.commit()
        except Exception:
            self.raw.rollback()
            raise

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()


def connect_db() -> DatabaseConnection:
    if DATABASE_URL:
        connection = psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row, connect_timeout=10)
        return DatabaseConnection(connection, postgres=True)
    connection = sqlite3.connect(DATABASE, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return DatabaseConnection(connection, postgres=False)


@contextmanager
def database_connection():
    connection = connect_db()
    try:
        yield connection
    finally:
        connection.close()


def initialize_database() -> None:
    with database_connection() as connection:
        connection.executescript(SCHEMA)


@contextmanager
def atomic():
    connection = get_db()
    with connection.transaction():
        yield connection


@app.before_request
def open_database() -> None:
    g.db = connect_db()
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        expected = session.get("csrf_token", "")
        received = request.headers.get("X-CSRF-Token", "")
        if not expected or not hmac.compare_digest(expected, received):
            return jsonify(error="Session expired. Refresh the page and try again."), 403


@app.teardown_appcontext
def close_database(_error: BaseException | None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def get_db() -> DatabaseConnection:
    if "db" not in g:
        g.db = connect_db()
    return g.db


def fail(message: str, status: int = 400):
    return jsonify(error=message), status


def request_data() -> dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def normalize_phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("233") and len(digits) == 12:
        return digits
    if digits.startswith("0") and len(digits) == 10:
        return "233" + digits[1:]
    if len(digits) == 9:
        return "233" + digits
    raise ValueError("Enter a valid Ghanaian phone number.")


def parse_amount(value: Any) -> int:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a valid amount.") from None
    if not amount.is_finite() or amount <= 0 or amount > Decimal("100000"):
        raise ValueError("Amount must be greater than zero and no more than GH₵100,000.")
    return int(amount * 100)


def public_user(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"], "name": row["name"], "phone": row["phone"],
        "role": row["role"], "balance_pesewas": row["balance_pesewas"],
        "banned": bool(row["banned"]), "created_at": row["created_at"],
    }


def current_user() -> Mapping[str, Any] | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def login_required(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return fail("Sign in to continue.", 401)
        if user["banned"]:
            session.clear()
            return fail("This account is disabled. Contact the business manager.", 403)
        g.user = user
        return function(*args, **kwargs)
    return wrapped


def admin_required(function):
    @wraps(function)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "admin":
            return fail("Administrator access is required.", 403)
        return function(*args, **kwargs)
    return wrapped


def create_transaction(
    connection: DatabaseConnection,
    user_id: str,
    transaction_type: str,
    amount_pesewas: int,
    status: str,
    description: str,
    *,
    reference: str | None = None,
    provider: str | None = None,
    payout_phone: str | None = None,
    admin_id: str | None = None,
    created_at: int | None = None,
) -> str:
    transaction_id = str(uuid.uuid4())
    now = created_at or int(time.time())
    connection.execute(
        """INSERT INTO transactions
        (id, user_id, type, amount_pesewas, status, description, reference, provider, payout_phone, admin_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (transaction_id, user_id, transaction_type, amount_pesewas, status, description,
         reference, provider, payout_phone, admin_id, now, now),
    )
    return transaction_id


def accrue_dividends(user_id: str | None = None) -> None:
    now = int(time.time())
    with atomic() as connection:
        query = "SELECT * FROM investments WHERE started_at <= ?"
        parameters: tuple[Any, ...] = (now,)
        if user_id:
            query += " AND user_id = ?"
            parameters += (user_id,)
        investments = connection.execute(query, parameters).fetchall()
        for investment in investments:
            earned_days = min(investment["duration_days"], max(0, (now - investment["started_at"]) // 86400))
            existing_days = {
                row["day"] for row in connection.execute(
                    "SELECT day FROM dividend_payouts WHERE investment_id = ?", (investment["id"],)
                )
            }
            for day in range(1, earned_days + 1):
                if day in existing_days:
                    continue
                payout_id = create_transaction(
                    connection, investment["user_id"], "dividend", investment["daily_pesewas"],
                    "paid", f"Daily dividend · {investment['product_name']}", created_at=now,
                )
                connection.execute(
                    "INSERT INTO dividend_payouts (investment_id, day, transaction_id, created_at) VALUES (?, ?, ?, ?)",
                    (investment["id"], day, payout_id, now),
                )
                connection.execute(
                    "UPDATE users SET balance_pesewas = balance_pesewas + ? WHERE id = ?",
                    (investment["daily_pesewas"], investment["user_id"]),
                )


def api_transaction(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in (
        "id", "user_id", "type", "amount_pesewas", "status", "description",
        "reference", "provider", "payout_phone", "admin_id", "created_at", "updated_at",
    )}


def login_rate_limited() -> bool:
    now = time.time()
    key = request.remote_addr or "unknown"
    with _login_lock:
        attempts = _login_attempts[key]
        while attempts and now - attempts[0] > 900:
            attempts.popleft()
        if len(attempts) >= 10:
            return True
        attempts.append(now)
        return False


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/health")
def health():
    try:
        get_db().execute("SELECT 1")
    except DATABASE_ERRORS:
        return jsonify(status="unhealthy"), 503
    return jsonify(status="ok")


@app.get("/app.js")
def frontend_script():
    return send_from_directory(BASE_DIR, "app.js")


@app.get("/api/session")
def session_info():
    csrf_token = session.get("csrf_token")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["csrf_token"] = csrf_token
    user = current_user()
    return jsonify(csrf_token=csrf_token, user=public_user(user) if user and not user["banned"] else None)


@app.post("/api/auth/register")
def register():
    data = request_data()
    try:
        name = str(data.get("name", "")).strip()
        phone = normalize_phone(data.get("phone"))
    except ValueError as error:
        return fail(str(error))
    password = str(data.get("password", ""))
    if not name or len(name) > 80:
        return fail("Enter your full name (up to 80 characters).")
    if len(password) < 12 or len(password) > 256:
        return fail("Use a password between 12 and 256 characters.")
    user_id = str(uuid.uuid4())
    try:
        with atomic() as connection:
            connection.execute(
                "INSERT INTO users (id, name, phone, password_hash, role, created_at) VALUES (?, ?, ?, ?, 'investor', ?)" ,
                (user_id, name, phone, generate_password_hash(password, method="scrypt"), int(time.time())),
            )
    except DATABASE_INTEGRITY_ERRORS:
        return fail("An account already exists for that phone number.", 409)
    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    session["csrf_token"] = secrets.token_urlsafe(32)
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return jsonify(user=public_user(row), csrf_token=session["csrf_token"]), 201


@app.post("/api/auth/login")
def login():
    if login_rate_limited():
        return fail("Too many sign-in attempts. Try again in 15 minutes.", 429)
    data = request_data()
    try:
        phone = normalize_phone(data.get("phone"))
    except ValueError:
        phone = ""
    row = get_db().execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
    password = str(data.get("password", ""))
    valid = row is not None and check_password_hash(row["password_hash"], password)
    if not valid:
        return fail("Phone number or password is incorrect.", 401)
    if row["banned"]:
        return fail("This account is disabled. Contact the business manager.", 403)
    session.clear()
    session.permanent = True
    session["user_id"] = row["id"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify(user=public_user(row), csrf_token=session["csrf_token"])


@app.post("/api/auth/logout")
@login_required
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/dashboard")
@login_required
def dashboard():
    if not PREVIEW_MODE:
        accrue_dividends(g.user["id"])
    connection = get_db()
    user = connection.execute("SELECT * FROM users WHERE id = ?", (g.user["id"],)).fetchone()
    transactions = connection.execute(
        "SELECT * FROM transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT 100", (user["id"],)
    ).fetchall()
    investments = connection.execute(
        """SELECT i.*, COALESCE(SUM(t.amount_pesewas), 0) AS earned_pesewas
        FROM investments i LEFT JOIN dividend_payouts dp ON dp.investment_id = i.id
        LEFT JOIN transactions t ON t.id = dp.transaction_id
        WHERE i.user_id = ? GROUP BY i.id ORDER BY i.started_at DESC""", (user["id"],)
    ).fetchall()
    messages = connection.execute(
        "SELECT id, message, created_at FROM messages WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
        (user["id"],),
    ).fetchall()
    return jsonify(
        user=public_user(user), products=PRODUCTS, preview_mode=PREVIEW_MODE,
        transactions=[api_transaction(row) for row in transactions],
        investments=[{key: row[key] for key in (
            "id", "product_id", "product_name", "price_pesewas", "daily_pesewas",
            "duration_days", "started_at", "earned_pesewas",
        )} for row in investments],
        messages=[dict(row) for row in messages],
    )


@app.post("/api/deposits")
@login_required
def request_deposit():
    if PREVIEW_MODE:
        return fail("Deposits are disabled on this free preview deployment.", 503)
    data = request_data()
    try:
        amount = parse_amount(data.get("amount"))
    except ValueError as error:
        return fail(str(error))
    reference = str(data.get("reference", "")).strip()
    if not reference or len(reference) > 40:
        return fail("Enter the phone number used to send the MoMo transfer as the reference.")
    with atomic() as connection:
        transaction_id = create_transaction(
            connection, g.user["id"], "deposit", amount, "pending", "Manual deposit request",
            reference=reference,
        )
    return jsonify(id=transaction_id, status="pending"), 201


@app.post("/api/withdrawals")
@login_required
def request_withdrawal():
    if PREVIEW_MODE:
        return fail("Withdrawals are disabled on this free preview deployment.", 503)
    data = request_data()
    try:
        amount = parse_amount(data.get("amount"))
        phone = normalize_phone(data.get("phone"))
    except ValueError as error:
        return fail(str(error))
    provider_id = str(data.get("provider", ""))
    provider = PROVIDER_NAMES.get(provider_id)
    if provider is None:
        return fail("Choose a supported mobile money provider.")
    with atomic() as connection:
        updated = connection.execute(
            "UPDATE users SET balance_pesewas = balance_pesewas - ? WHERE id = ? AND balance_pesewas >= ? AND banned = 0",
            (amount, g.user["id"], amount),
        )
        if updated.rowcount != 1:
            return fail("Insufficient available balance.", 409)
        transaction_id = create_transaction(
            connection, g.user["id"], "withdrawal", amount, "pending", "Manual withdrawal request",
            provider=provider, payout_phone=phone,
        )
    return jsonify(id=transaction_id, status="pending"), 201


@app.post("/api/investments")
@login_required
def purchase_investment():
    if PREVIEW_MODE:
        return fail("Investments are disabled on this free preview deployment.", 503)
    data = request_data()
    product = next((item for item in PRODUCTS if item["id"] == data.get("product_id")), None)
    if product is None:
        return fail("That investment product is not available.", 404)
    investment_id = str(uuid.uuid4())
    now = int(time.time())
    with atomic() as connection:
        updated = connection.execute(
            "UPDATE users SET balance_pesewas = balance_pesewas - ? WHERE id = ? AND balance_pesewas >= ? AND banned = 0",
            (product["price_pesewas"], g.user["id"], product["price_pesewas"]),
        )
        if updated.rowcount != 1:
            return fail("Insufficient available balance.", 409)
        connection.execute(
            """INSERT INTO investments
            (id, user_id, product_id, product_name, price_pesewas, daily_pesewas, duration_days, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (investment_id, g.user["id"], product["id"], product["name"], product["price_pesewas"],
             product["daily_pesewas"], product["duration_days"], now),
        )
        create_transaction(
            connection, g.user["id"], "purchase", product["price_pesewas"], "paid",
            f"Investment purchase · {product['name']}", created_at=now,
        )
    return jsonify(id=investment_id), 201


@app.get("/api/admin")
@admin_required
def admin_dashboard():
    users = get_db().execute("SELECT * FROM users ORDER BY created_at").fetchall()
    if not PREVIEW_MODE:
        for user in users:
            accrue_dividends(user["id"])
    connection = get_db()
    transactions = connection.execute(
        """SELECT t.*, u.name AS user_name, u.phone AS user_phone
        FROM transactions t JOIN users u ON u.id = t.user_id
        WHERE t.type IN ('deposit', 'withdrawal') ORDER BY t.created_at DESC LIMIT 300"""
    ).fetchall()
    users = connection.execute("SELECT * FROM users ORDER BY created_at").fetchall()
    return jsonify(
        users=[public_user(row) for row in users],
        requests=[{**api_transaction(row), "user_name": row["user_name"], "user_phone": row["user_phone"]} for row in transactions],
    )


@app.post("/api/admin/requests/<transaction_id>/review")
@admin_required
def review_request(transaction_id: str):
    if PREVIEW_MODE:
        return fail("Payment settlement is disabled on this free preview deployment.", 503)
    action = str(request_data().get("action", ""))
    if action not in {"approve", "reject", "paid"}:
        return fail("Choose approve, reject, or paid.")
    with atomic() as connection:
        transaction = connection.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
        if transaction is None or transaction["type"] not in {"deposit", "withdrawal"}:
            return fail("Request not found.", 404)
        if transaction["status"] != "pending":
            return fail("This request has already been reviewed.", 409)
        if transaction["type"] == "deposit" and action == "approve":
            connection.execute(
                "UPDATE users SET balance_pesewas = balance_pesewas + ? WHERE id = ?",
                (transaction["amount_pesewas"], transaction["user_id"]),
            )
            status = "approved"
        elif transaction["type"] == "deposit" and action == "reject":
            status = "rejected"
        elif transaction["type"] == "withdrawal" and action == "paid":
            status = "paid"
        elif transaction["type"] == "withdrawal" and action == "reject":
            connection.execute(
                "UPDATE users SET balance_pesewas = balance_pesewas + ? WHERE id = ?",
                (transaction["amount_pesewas"], transaction["user_id"]),
            )
            status = "rejected"
        else:
            return fail("That action is not valid for this request type.")
        connection.execute(
            "UPDATE transactions SET status = ?, admin_id = ?, updated_at = ? WHERE id = ? AND status = 'pending'",
            (status, g.user["id"], int(time.time()), transaction_id),
        )
    return jsonify(ok=True, status=status)


@app.post("/api/admin/users/<user_id>/balance")
@admin_required
def adjust_balance(user_id: str):
    if PREVIEW_MODE:
        return fail("Balance adjustments are disabled on this free preview deployment.", 503)
    data = request_data()
    action = str(data.get("action", ""))
    if action not in {"credit", "debit"}:
        return fail("Choose credit or debit.")
    try:
        amount = parse_amount(data.get("amount"))
    except ValueError as error:
        return fail(str(error))
    note = str(data.get("note", "")).strip()[:120]
    with atomic() as connection:
        target = connection.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if target is None:
            return fail("Account not found.", 404)
        if action == "credit":
            connection.execute("UPDATE users SET balance_pesewas = balance_pesewas + ? WHERE id = ?", (amount, user_id))
        else:
            result = connection.execute(
                "UPDATE users SET balance_pesewas = balance_pesewas - ? WHERE id = ? AND balance_pesewas >= ?",
                (amount, user_id, amount),
            )
            if result.rowcount != 1:
                return fail("Debit exceeds the available balance.", 409)
        create_transaction(
            connection, user_id, action, amount, "approved", f"Administrator {action}" + (f" · {note}" if note else ""),
            admin_id=g.user["id"],
        )
    return jsonify(ok=True)


@app.post("/api/admin/users/<user_id>/status")
@admin_required
def update_account_status(user_id: str):
    banned = bool(request_data().get("banned"))
    if user_id == g.user["id"] and banned:
        return fail("You cannot disable your own administrator account.")
    with atomic() as connection:
        target = connection.execute("SELECT id, role FROM users WHERE id = ?", (user_id,)).fetchone()
        if target is None:
            return fail("Account not found.", 404)
        if target["role"] == "admin":
            return fail("Administrator accounts cannot be disabled from this panel.", 409)
        connection.execute("UPDATE users SET banned = ? WHERE id = ?", (int(banned), user_id))
        if banned:
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return jsonify(ok=True, banned=banned)


@app.post("/api/admin/users/<user_id>/messages")
@admin_required
def send_message(user_id: str):
    message = str(request_data().get("message", "")).strip()
    if not message or len(message) > 1000:
        return fail("Message must contain 1 to 1,000 characters.")
    message_id = str(uuid.uuid4())
    with atomic() as connection:
        target = connection.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if target is None:
            return fail("Account not found.", 404)
        connection.execute(
            "INSERT INTO messages (id, user_id, admin_id, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (message_id, user_id, g.user["id"], message, int(time.time())),
        )
    return jsonify(id=message_id), 201


def provision_admin() -> None:
    initialize_database()
    with database_connection() as connection:
        if connection.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
            raise SystemExit("An administrator already exists.")
    name = input("Administrator full name: ").strip()
    try:
        phone = normalize_phone(input("Ghana phone number: "))
    except ValueError as error:
        raise SystemExit(str(error)) from None
    password = getpass.getpass("Administrator password (12+ characters): ")
    if len(password) < 12:
        raise SystemExit("Use an administrator password of at least 12 characters.")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords did not match.")
    with database_connection() as connection:
        connection.execute(
            "INSERT INTO users (id, name, phone, password_hash, role, created_at) VALUES (?, ?, ?, ?, 'admin', ?)",
            (str(uuid.uuid4()), name, phone, generate_password_hash(password, method="scrypt"), int(time.time())),
        )
    print("Administrator account created. Keep this machine and its database access restricted.")


if __name__ == "__main__":
    initialize_database()
    if len(sys.argv) > 1 and sys.argv[1] == "init-admin":
        provision_admin()
    else:
        if os.environ.get("INVESTIT_ENV") == "production" and not os.environ.get("INVESTIT_SECRET_KEY"):
            raise SystemExit("Set INVESTIT_SECRET_KEY before starting in production.")
        host = os.environ.get("INVESTIT_HOST", "127.0.0.1")
        port = int(os.environ.get("PORT", "8000"))
        serve(app, host=host, port=port)
