"""订阅订单、通知、退款与权益的 SQLite 存储。

与账号服务共用同一个数据库：订单只引用 ``accounts.user_id``，下单时固定归属，
支付人（``buyer_id``）只用于交易核对，不作为身份。旧 ``relay.sqlite3`` 不迁移。
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hastings.accounts.db import AccountDatabase
from hastings.accounts.store import AccountsStore
from hastings.relay.plans import Plan, get_plan

WINDOW_SECONDS = 24 * 60 * 60
PAID_TRADE_STATUSES = frozenset({"TRADE_SUCCESS", "TRADE_FINISHED"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS relay_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_orders (
    out_trade_no TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    plan TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    subject TEXT NOT NULL,
    status TEXT NOT NULL,
    trade_no TEXT,
    buyer_id TEXT,
    created_at TEXT NOT NULL,
    paid_at TEXT,
    closed_at TEXT,
    last_checked_at TEXT,
    query_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    raw TEXT NOT NULL DEFAULT '',
    UNIQUE (user_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_billing_orders_user
    ON billing_orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_billing_orders_pending
    ON billing_orders(status, last_checked_at);

CREATE TABLE IF NOT EXISTS billing_entitlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    plan TEXT NOT NULL,
    daily_points INTEGER NOT NULL,
    total_points INTEGER NOT NULL,
    starts_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    order_no TEXT NOT NULL UNIQUE REFERENCES billing_orders(out_trade_no),
    refunded_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_billing_entitlements_user
    ON billing_entitlements(user_id, expires_at);

CREATE TABLE IF NOT EXISTS billing_notify_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notify_id TEXT NOT NULL UNIQUE,
    out_trade_no TEXT NOT NULL,
    trade_no TEXT,
    trade_status TEXT NOT NULL,
    raw TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_refunds (
    out_request_no TEXT PRIMARY KEY,
    out_trade_no TEXT NOT NULL REFERENCES billing_orders(out_trade_no),
    user_id TEXT NOT NULL REFERENCES accounts(user_id),
    amount_cents INTEGER NOT NULL,
    status TEXT NOT NULL,
    fund_change TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    raw TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS billing_refund_requests (
    out_request_no TEXT PRIMARY KEY,
    out_trade_no TEXT NOT NULL REFERENCES billing_orders(out_trade_no),
    user_id TEXT NOT NULL REFERENCES accounts(user_id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, out_trade_no)
);

CREATE TABLE IF NOT EXISTS usage_windows (
    user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    window_start TEXT NOT NULL,
    points_used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, window_start)
);

CREATE TABLE IF NOT EXISTS usage_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    cached_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    points INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, request_id)
);

CREATE TABLE IF NOT EXISTS usage_reservations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL,
    entitlement_order_no TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_resets_at TEXT NOT NULL,
    model TEXT NOT NULL,
    reserved_micro INTEGER NOT NULL,
    settled_micro INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    settled_at TEXT,
    error TEXT NOT NULL DEFAULT '',
    UNIQUE (user_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_usage_reservations_user
    ON usage_reservations(user_id, status, expires_at);
"""


class RefundConflictError(ValueError):
    """退款请求号金额不一致，或累计退款超过订单实付金额。"""


class QuotaExceeded(ValueError):
    """当前窗口或整期额度不足以预留。"""


class ConcurrencyExceeded(ValueError):
    """同一账号同时进行的模型请求达到上限。"""


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _window_start(starts_at: str, now: str) -> tuple[str, str]:
    start = parse_ts(starts_at)
    current = parse_ts(now)
    if current < start:
        reset = start + timedelta(seconds=WINDOW_SECONDS)
        return start.isoformat(), reset.isoformat()
    index = int((current - start).total_seconds() // WINDOW_SECONDS)
    window = start + timedelta(seconds=index * WINDOW_SECONDS)
    return window.isoformat(), (window + timedelta(seconds=WINDOW_SECONDS)).isoformat()


@dataclass(frozen=True, slots=True)
class Order:
    out_trade_no: str
    user_id: str
    request_id: str
    plan: str
    amount_cents: int
    subject: str
    status: str
    trade_no: str
    buyer_id: str
    created_at: str
    paid_at: str
    closed_at: str
    last_checked_at: str
    query_attempts: int
    last_error: str
    raw: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Order:
        return cls(
            out_trade_no=str(row["out_trade_no"]),
            user_id=str(row["user_id"]),
            request_id=str(row["request_id"]),
            plan=str(row["plan"]),
            amount_cents=int(row["amount_cents"]),
            subject=str(row["subject"]),
            status=str(row["status"]),
            trade_no=str(row["trade_no"] or ""),
            buyer_id=str(row["buyer_id"] or ""),
            created_at=str(row["created_at"]),
            paid_at=str(row["paid_at"] or ""),
            closed_at=str(row["closed_at"] or ""),
            last_checked_at=str(row["last_checked_at"] or ""),
            query_attempts=int(row["query_attempts"] or 0),
            last_error=str(row["last_error"] or ""),
            raw=str(row["raw"] or ""),
        )


@dataclass(frozen=True, slots=True)
class Entitlement:
    id: int
    user_id: str
    plan: str
    daily_points: int
    total_points: int
    starts_at: str
    expires_at: str
    order_no: str
    refunded_at: str
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Entitlement:
        return cls(
            id=int(row["id"]),
            user_id=str(row["user_id"]),
            plan=str(row["plan"]),
            daily_points=int(row["daily_points"]),
            total_points=int(row["total_points"]),
            starts_at=str(row["starts_at"]),
            expires_at=str(row["expires_at"]),
            order_no=str(row["order_no"]),
            refunded_at=str(row["refunded_at"] or ""),
            created_at=str(row["created_at"]),
        )


@dataclass(frozen=True, slots=True)
class Reservation:
    id: str
    user_id: str
    workspace_id: str
    request_id: str
    entitlement_order_no: str
    window_start: str
    window_resets_at: str
    model: str
    reserved_micro: int
    settled_micro: int
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    status: str
    created_at: str
    expires_at: str
    settled_at: str
    error: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Reservation:
        return cls(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            workspace_id=str(row["workspace_id"] or ""),
            request_id=str(row["request_id"]),
            entitlement_order_no=str(row["entitlement_order_no"]),
            window_start=str(row["window_start"]),
            window_resets_at=str(row["window_resets_at"]),
            model=str(row["model"]),
            reserved_micro=int(row["reserved_micro"]),
            settled_micro=int(row["settled_micro"]),
            input_tokens=int(row["input_tokens"]),
            cached_tokens=int(row["cached_tokens"]),
            output_tokens=int(row["output_tokens"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
            settled_at=str(row["settled_at"] or ""),
            error=str(row["error"] or ""),
        )


@dataclass(frozen=True, slots=True)
class RefundRecord:
    out_request_no: str
    out_trade_no: str
    user_id: str
    amount_cents: int
    status: str
    fund_change: str
    created_at: str
    updated_at: str
    raw: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RefundRecord:
        return cls(
            out_request_no=str(row["out_request_no"]),
            out_trade_no=str(row["out_trade_no"]),
            user_id=str(row["user_id"]),
            amount_cents=int(row["amount_cents"]),
            status=str(row["status"]),
            fund_change=str(row["fund_change"] or ""),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            raw=str(row["raw"] or ""),
        )


@dataclass(frozen=True, slots=True)
class RefundRequest:
    out_request_no: str
    out_trade_no: str
    user_id: str
    reason: str
    status: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RefundRequest:
        return cls(
            out_request_no=str(row["out_request_no"]),
            out_trade_no=str(row["out_trade_no"]),
            user_id=str(row["user_id"]),
            reason=str(row["reason"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )


class RelayStore:
    """共享账号库上的支付表；初始化时同时确保账号表存在。"""

    def __init__(self, database: AccountDatabase) -> None:
        self._db = database
        self.accounts = AccountsStore(database)

    @classmethod
    def from_path(cls, path: Path) -> RelayStore:
        return cls(AccountDatabase(Path(path).expanduser()))

    @property
    def path(self) -> Path:
        return self._db.path

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._db.read() as connection:
            yield connection

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._db.write() as connection:
            yield connection

    def initialize(self) -> None:
        self._db.initialize()
        with self._read() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO relay_meta(key, value) VALUES('return_secret', ?)",
                (secrets.token_urlsafe(32),),
            )

    def ping(self) -> None:
        with self._read() as connection:
            connection.execute("SELECT 1").fetchone()

    def return_secret(self) -> str:
        with self._read() as connection:
            row = connection.execute(
                "SELECT value FROM relay_meta WHERE key = 'return_secret'",
            ).fetchone()
        if row is None:
            raise RuntimeError("return secret not initialized")
        return str(row["value"])

    # -- 订单 ---------------------------------------------------------------

    def create_order(
        self,
        *,
        out_trade_no: str,
        user_id: str,
        request_id: str,
        plan: Plan,
        subject: str,
        now: str,
    ) -> Order:
        with self._write() as connection:
            connection.execute(
                "INSERT INTO billing_orders(out_trade_no, user_id, request_id, plan, "
                "amount_cents, subject, status, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, 'PENDING', ?)",
                (out_trade_no, user_id, request_id, plan.code, plan.price_cents, subject, now),
            )
        order = self.get_order(out_trade_no)
        if order is None:
            raise RuntimeError("order insert did not persist")
        return order

    def find_order_by_request(self, user_id: str, request_id: str) -> Order | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM billing_orders WHERE user_id = ? AND request_id = ?",
                (user_id, request_id),
            ).fetchone()
        return Order.from_row(row) if row is not None else None

    def get_order(self, out_trade_no: str) -> Order | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM billing_orders WHERE out_trade_no = ?",
                (out_trade_no,),
            ).fetchone()
        return Order.from_row(row) if row is not None else None

    def pending_order_for_plan(self, user_id: str, plan: str) -> Order | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM billing_orders WHERE user_id = ? AND plan = ? "
                "AND status = 'PENDING' ORDER BY rowid DESC LIMIT 1",
                (user_id, plan),
            ).fetchone()
        return Order.from_row(row) if row is not None else None

    def experience_paid_exists(self, user_id: str) -> bool:
        """体验卡是否已经买过（付款成功，或付款后又退款）。未支付的订单不算。"""
        with self._read() as connection:
            row = connection.execute(
                "SELECT 1 FROM billing_orders WHERE user_id = ? AND plan = 'experience' "
                "AND status IN ('PAID', 'REFUNDED') LIMIT 1",
                (user_id,),
            ).fetchone()
        return row is not None

    def mark_paid(
        self,
        out_trade_no: str,
        *,
        trade_no: str,
        buyer_id: str,
        raw: str,
        now: str,
    ) -> Entitlement:
        with self._write() as connection:
            return self._mark_paid_locked(
                connection,
                out_trade_no,
                trade_no=trade_no,
                buyer_id=buyer_id,
                raw=raw,
                now=now,
            )

    def _mark_paid_locked(
        self,
        connection: sqlite3.Connection,
        out_trade_no: str,
        *,
        trade_no: str,
        buyer_id: str,
        raw: str,
        now: str,
    ) -> Entitlement:
        row = connection.execute(
            "SELECT * FROM billing_orders WHERE out_trade_no = ?",
            (out_trade_no,),
        ).fetchone()
        if row is None:
            raise ValueError("order not found")
        order = Order.from_row(row)
        if order.status == "REFUNDED":
            raise ValueError("order already refunded")
        existing = connection.execute(
            "SELECT * FROM billing_entitlements WHERE order_no = ?",
            (out_trade_no,),
        ).fetchone()
        if existing is not None:
            return Entitlement.from_row(existing)

        plan = get_plan(order.plan)
        if plan is None:
            raise ValueError("unknown plan")
        connection.execute(
            "UPDATE billing_orders SET status = 'PAID', trade_no = ?, buyer_id = ?, "
            "paid_at = ?, raw = ?, last_error = '' WHERE out_trade_no = ?",
            (trade_no, buyer_id, now, raw, out_trade_no),
        )
        latest = connection.execute(
            "SELECT starts_at, expires_at FROM billing_entitlements "
            "WHERE user_id = ? AND refunded_at IS NULL AND expires_at > ? "
            "ORDER BY expires_at DESC LIMIT 1",
            (order.user_id, now),
        ).fetchone()
        # 续费沿用当前有效期的窗口锚点，避免付款后重置当日额度。
        starts_at = str(latest["starts_at"]) if latest is not None else now
        current_expiry = str(latest["expires_at"]) if latest is not None else now
        expires_at = max(parse_ts(current_expiry), parse_ts(now)) + timedelta(days=plan.days)
        connection.execute(
            "INSERT INTO billing_entitlements(user_id, plan, daily_points, total_points, "
            "starts_at, expires_at, order_no, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                order.user_id,
                plan.code,
                plan.daily_points,
                plan.total_points,
                starts_at,
                expires_at.isoformat(),
                out_trade_no,
                now,
            ),
        )
        entitlement = connection.execute(
            "SELECT * FROM billing_entitlements WHERE order_no = ?",
            (out_trade_no,),
        ).fetchone()
        if entitlement is None:
            raise RuntimeError("entitlement insert did not persist")
        return Entitlement.from_row(entitlement)

    def process_notify(
        self,
        *,
        notify_id: str,
        out_trade_no: str,
        trade_no: str,
        trade_status: str,
        buyer_id: str,
        raw: str,
        is_paid: bool,
        now: str,
    ) -> bool:
        with self._write() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO billing_notify_events(notify_id, out_trade_no, "
                "trade_no, trade_status, raw, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (notify_id, out_trade_no, trade_no, trade_status, raw, now),
            )
            if cursor.rowcount == 0:
                return False
            if is_paid:
                self._mark_paid_locked(
                    connection,
                    out_trade_no,
                    trade_no=trade_no,
                    buyer_id=buyer_id,
                    raw=raw,
                    now=now,
                )
            return True

    def update_from_query(
        self,
        out_trade_no: str,
        *,
        trade_status: str,
        trade_no: str,
        buyer_id: str,
        raw: str,
        now: str,
    ) -> Order:
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM billing_orders WHERE out_trade_no = ?",
                (out_trade_no,),
            ).fetchone()
            if row is None:
                raise ValueError("order not found")
            order = Order.from_row(row)
            connection.execute(
                "UPDATE billing_orders SET last_checked_at = ?, query_attempts = "
                "query_attempts + 1, last_error = '', raw = ? WHERE out_trade_no = ?",
                (now, raw, out_trade_no),
            )
            if trade_status in PAID_TRADE_STATUSES:
                self._mark_paid_locked(
                    connection,
                    out_trade_no,
                    trade_no=trade_no,
                    buyer_id=buyer_id,
                    raw=raw,
                    now=now,
                )
            elif trade_status == "TRADE_CLOSED" and order.status == "PENDING":
                connection.execute(
                    "UPDATE billing_orders SET status = 'CLOSED', closed_at = ? "
                    "WHERE out_trade_no = ?",
                    (now, out_trade_no),
                )
        updated = self.get_order(out_trade_no)
        if updated is None:
            raise RuntimeError("order disappeared")
        return updated

    def mark_checked(self, out_trade_no: str, now: str, *, error: str = "") -> None:
        with self._write() as connection:
            connection.execute(
                "UPDATE billing_orders SET last_checked_at = ?, query_attempts = "
                "query_attempts + 1, last_error = ? WHERE out_trade_no = ?",
                (now, error, out_trade_no),
            )

    def pending_orders(self, *, checked_before: str, limit: int) -> tuple[Order, ...]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM billing_orders WHERE status = 'PENDING' "
                "AND (last_checked_at IS NULL OR last_checked_at <= ?) "
                "ORDER BY created_at LIMIT ?",
                (checked_before, limit),
            ).fetchall()
        return tuple(Order.from_row(row) for row in rows)

    def close_order(self, out_trade_no: str, now: str) -> Order:
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM billing_orders WHERE out_trade_no = ?",
                (out_trade_no,),
            ).fetchone()
            if row is None:
                raise ValueError("order not found")
            order = Order.from_row(row)
            if order.status == "PAID":
                raise ValueError("paid order cannot be closed")
            if order.status == "PENDING":
                connection.execute(
                    "UPDATE billing_orders SET status = 'CLOSED', closed_at = ? "
                    "WHERE out_trade_no = ?",
                    (now, out_trade_no),
                )
        updated = self.get_order(out_trade_no)
        if updated is None:
            raise RuntimeError("order disappeared")
        return updated

    # -- 权益 ---------------------------------------------------------------

    def current_entitlement(self, user_id: str, now: str) -> Entitlement | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM billing_entitlements WHERE user_id = ? "
                "AND refunded_at IS NULL AND expires_at > ? "
                "ORDER BY expires_at DESC LIMIT 1",
                (user_id, now),
            ).fetchone()
        return Entitlement.from_row(row) if row is not None else None

    def entitlement_summary(self, user_id: str, now: str) -> dict[str, object]:
        entitlement = self.current_entitlement(user_id, now)
        if entitlement is None:
            return {
                "active": False,
                "plan": None,
                "expires_at": None,
                "daily_points": 0,
                "daily_used": 0.0,
                "daily_reserved": 0.0,
                "daily_remaining": None,
                "daily_percent": None,
                "total_points": 0,
                "total_used": 0.0,
                "total_reserved": 0.0,
                "total_remaining": None,
                "total_percent": None,
                "window_resets_at": None,
            }
        window_start, window_resets = _window_start(entitlement.starts_at, now)
        with self._read() as connection:
            daily_row = connection.execute(
                "SELECT points_used FROM usage_windows WHERE user_id = ? AND window_start = ?",
                (user_id, window_start),
            ).fetchone()
            total_row = connection.execute(
                "SELECT COALESCE(SUM(points_used), 0) AS points_used FROM usage_windows "
                "WHERE user_id = ? AND window_start >= ?",
                (user_id, entitlement.starts_at),
            ).fetchone()
            daily_reserved_row = connection.execute(
                "SELECT COALESCE(SUM(reserved_micro), 0) AS reserved FROM usage_reservations "
                "WHERE user_id = ? AND window_start = ? AND status IN ('reserved', 'review') "
                "AND expires_at > ?",
                (user_id, window_start, now),
            ).fetchone()
            total_reserved_row = connection.execute(
                "SELECT COALESCE(SUM(reserved_micro), 0) AS reserved FROM usage_reservations "
                "WHERE user_id = ? AND entitlement_order_no = ? "
                "AND status IN ('reserved', 'review') AND expires_at > ?",
                (user_id, entitlement.order_no, now),
            ).fetchone()
        daily_used = int(daily_row["points_used"]) if daily_row is not None else 0
        total_used = int(total_row["points_used"]) if total_row is not None else 0
        daily_reserved = int(daily_reserved_row["reserved"]) if daily_reserved_row else 0
        total_reserved = int(total_reserved_row["reserved"]) if total_reserved_row else 0

        def points(micro: int) -> float:
            return round(micro / 1_000_000, 3)

        daily_limit = entitlement.daily_points * 1_000_000
        total_limit = entitlement.total_points * 1_000_000
        daily_remaining = (
            max(0, daily_limit - daily_used - daily_reserved) if daily_limit > 0 else None
        )
        total_remaining = (
            max(0, total_limit - total_used - total_reserved) if total_limit > 0 else None
        )
        return {
            "active": True,
            "plan": entitlement.plan,
            "expires_at": entitlement.expires_at,
            "daily_points": entitlement.daily_points,
            "daily_used": points(daily_used),
            "daily_reserved": points(daily_reserved),
            "daily_remaining": points(daily_remaining) if daily_remaining is not None else None,
            "daily_percent": (
                round((daily_used + daily_reserved) / daily_limit * 100, 1)
                if daily_limit > 0
                else None
            ),
            "total_points": entitlement.total_points,
            "total_used": points(total_used),
            "total_reserved": points(total_reserved),
            "total_remaining": points(total_remaining) if total_remaining is not None else None,
            "total_percent": (
                round((total_used + total_reserved) / total_limit * 100, 1)
                if total_limit > 0
                else None
            ),
            "window_resets_at": window_resets,
        }

    # -- 用量预留 -----------------------------------------------------------

    def create_reservation(
        self,
        *,
        user_id: str,
        workspace_id: str,
        request_id: str,
        model: str,
        reserved_micro: int,
        ttl_seconds: int,
        max_concurrent: int,
        now: str,
    ) -> Reservation:
        with self._write() as connection:
            existing = connection.execute(
                "SELECT * FROM usage_reservations WHERE user_id = ? AND request_id = ?",
                (user_id, request_id),
            ).fetchone()
            if existing is not None:
                return Reservation.from_row(existing)
            entitlement = connection.execute(
                "SELECT * FROM billing_entitlements WHERE user_id = ? "
                "AND refunded_at IS NULL AND expires_at > ? "
                "ORDER BY expires_at DESC LIMIT 1",
                (user_id, now),
            ).fetchone()
            if entitlement is None:
                raise QuotaExceeded("no active entitlement")
            window_start, window_resets = _window_start(str(entitlement["starts_at"]), now)
            daily_points = int(entitlement["daily_points"])
            total_points = int(entitlement["total_points"])
            daily_used_row = connection.execute(
                "SELECT COALESCE(points_used, 0) AS used FROM usage_windows "
                "WHERE user_id = ? AND window_start = ?",
                (user_id, window_start),
            ).fetchone()
            daily_used = int(daily_used_row["used"]) if daily_used_row else 0
            daily_reserved_row = connection.execute(
                "SELECT COALESCE(SUM(reserved_micro), 0) AS reserved FROM usage_reservations "
                "WHERE user_id = ? AND window_start = ? AND status IN ('reserved', 'review') "
                "AND expires_at > ?",
                (user_id, window_start, now),
            ).fetchone()
            daily_reserved = int(daily_reserved_row["reserved"]) if daily_reserved_row else 0
            if daily_points > 0 and daily_used + daily_reserved + reserved_micro > (
                daily_points * 1_000_000
            ):
                raise QuotaExceeded("daily quota insufficient")
            total_used_row = connection.execute(
                "SELECT COALESCE(SUM(points_used), 0) AS used FROM usage_windows "
                "WHERE user_id = ? AND window_start >= ?",
                (user_id, str(entitlement["starts_at"])),
            ).fetchone()
            total_used = int(total_used_row["used"]) if total_used_row else 0
            total_reserved_row = connection.execute(
                "SELECT COALESCE(SUM(reserved_micro), 0) AS reserved FROM usage_reservations "
                "WHERE user_id = ? AND entitlement_order_no = ? "
                "AND status IN ('reserved', 'review') AND expires_at > ?",
                (user_id, str(entitlement["order_no"]), now),
            ).fetchone()
            total_reserved = int(total_reserved_row["reserved"]) if total_reserved_row else 0
            if total_points > 0 and total_used + total_reserved + reserved_micro > (
                total_points * 1_000_000
            ):
                raise QuotaExceeded("total quota insufficient")
            concurrent = connection.execute(
                "SELECT COUNT(*) AS count FROM usage_reservations "
                "WHERE user_id = ? AND status = 'reserved' AND expires_at > ?",
                (user_id, now),
            ).fetchone()
            if concurrent is not None and int(concurrent["count"]) >= max_concurrent:
                raise ConcurrencyExceeded("concurrent limit reached")
            reservation_id = "res_" + secrets.token_hex(16)
            expires_at = (parse_ts(now) + timedelta(seconds=ttl_seconds)).isoformat()
            connection.execute(
                "INSERT INTO usage_reservations(id, user_id, workspace_id, request_id, "
                "entitlement_order_no, window_start, window_resets_at, model, "
                "reserved_micro, status, created_at, expires_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?)",
                (
                    reservation_id,
                    user_id,
                    workspace_id,
                    request_id,
                    str(entitlement["order_no"]),
                    window_start,
                    window_resets,
                    model,
                    reserved_micro,
                    now,
                    expires_at,
                ),
            )
        reservation = self.get_reservation(reservation_id)
        if reservation is None:
            raise RuntimeError("reservation insert did not persist")
        return reservation

    def get_reservation(self, reservation_id: str) -> Reservation | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM usage_reservations WHERE id = ?",
                (reservation_id,),
            ).fetchone()
        return Reservation.from_row(row) if row is not None else None

    def reservation_by_request(self, user_id: str, request_id: str) -> Reservation | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM usage_reservations WHERE user_id = ? AND request_id = ?",
                (user_id, request_id),
            ).fetchone()
        return Reservation.from_row(row) if row is not None else None

    def settle_reservation(
        self,
        reservation_id: str,
        *,
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int,
        settled_micro: int,
        now: str,
    ) -> Reservation:
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM usage_reservations WHERE id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("reservation not found")
            reservation = Reservation.from_row(row)
            if reservation.status == "settled":
                return reservation
            entitlement = connection.execute(
                "SELECT refunded_at FROM billing_entitlements WHERE order_no = ?",
                (reservation.entitlement_order_no,),
            ).fetchone()
            if entitlement is None or entitlement["refunded_at"] is not None:
                # 退款与调用并发：权益已经结束，不再补记这笔调用。
                connection.execute(
                    "UPDATE usage_reservations SET status = 'released', settled_micro = 0, "
                    "error = 'entitlement_refunded', settled_at = ? WHERE id = ?",
                    (now, reservation_id),
                )
                released = connection.execute(
                    "SELECT * FROM usage_reservations WHERE id = ?",
                    (reservation_id,),
                ).fetchone()
                if released is None:
                    raise RuntimeError("reservation disappeared")
                return Reservation.from_row(released)
            cursor = connection.execute(
                "INSERT OR IGNORE INTO usage_ledger(user_id, request_id, model, "
                "input_tokens, cached_tokens, output_tokens, points, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    reservation.user_id,
                    reservation.request_id,
                    reservation.model,
                    input_tokens,
                    cached_tokens,
                    output_tokens,
                    settled_micro,
                    now,
                ),
            )
            if cursor.rowcount > 0:
                connection.execute(
                    "INSERT INTO usage_windows(user_id, window_start, points_used) "
                    "VALUES(?, ?, ?) ON CONFLICT(user_id, window_start) DO UPDATE SET "
                    "points_used = points_used + excluded.points_used",
                    (reservation.user_id, reservation.window_start, settled_micro),
                )
            connection.execute(
                "UPDATE usage_reservations SET status = 'settled', settled_micro = ?, "
                "input_tokens = ?, cached_tokens = ?, output_tokens = ?, settled_at = ?, "
                "error = '' WHERE id = ?",
                (
                    settled_micro,
                    input_tokens,
                    cached_tokens,
                    output_tokens,
                    now,
                    reservation_id,
                ),
            )
        updated = self.get_reservation(reservation_id)
        if updated is None:
            raise RuntimeError("reservation disappeared")
        return updated

    def mark_reservation_review(
        self,
        reservation_id: str,
        *,
        error: str,
        review_ttl_seconds: int,
        now: str,
    ) -> None:
        expires_at = (parse_ts(now) + timedelta(seconds=review_ttl_seconds)).isoformat()
        with self._write() as connection:
            connection.execute(
                "UPDATE usage_reservations SET status = 'review', error = ?, expires_at = ? "
                "WHERE id = ? AND status != 'settled'",
                (error, expires_at, reservation_id),
            )

    def release_reservation(self, reservation_id: str, *, error: str, now: str) -> None:
        with self._write() as connection:
            connection.execute(
                "UPDATE usage_reservations SET status = 'released', settled_micro = 0, "
                "error = ?, settled_at = ? WHERE id = ? AND status != 'settled'",
                (error, now, reservation_id),
            )

    def recover_usage_reservations(self, now: str) -> int:
        with self._write() as connection:
            cursor = connection.execute(
                "UPDATE usage_reservations SET status = 'released', "
                "error = CASE WHEN error = '' THEN 'expired' ELSE error END "
                "WHERE status IN ('reserved', 'review') AND expires_at <= ?",
                (now,),
            )
            return int(cursor.rowcount)

    # -- 退款 ---------------------------------------------------------------

    def create_refund(
        self,
        *,
        out_request_no: str,
        out_trade_no: str,
        amount_cents: int,
        now: str,
    ) -> RefundRecord:
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM billing_refunds WHERE out_request_no = ?",
                (out_request_no,),
            ).fetchone()
            if row is not None:
                record = RefundRecord.from_row(row)
                if record.amount_cents != amount_cents:
                    raise RefundConflictError("refund amount differs from existing request")
                return record
            order = connection.execute(
                "SELECT user_id, amount_cents FROM billing_orders WHERE out_trade_no = ?",
                (out_trade_no,),
            ).fetchone()
            if order is None:
                raise RefundConflictError("order not found")
            total = connection.execute(
                "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM billing_refunds "
                "WHERE out_trade_no = ? AND status IN ('PENDING', 'SUCCESS')",
                (out_trade_no,),
            ).fetchone()
            already = int(total["total"]) if total is not None else 0
            if already + amount_cents > int(order["amount_cents"]):
                raise RefundConflictError("refund exceeds order amount")
            connection.execute(
                "INSERT INTO billing_refunds(out_request_no, out_trade_no, user_id, "
                "amount_cents, status, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, 'PENDING', ?, ?)",
                (out_request_no, out_trade_no, str(order["user_id"]), amount_cents, now, now),
            )
        stored = self.get_refund(out_request_no)
        if stored is None:
            raise RuntimeError("refund insert did not persist")
        return stored

    def mark_refund_result(
        self,
        out_request_no: str,
        *,
        status: str,
        fund_change: str,
        raw: str,
        now: str,
    ) -> RefundRecord:
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM billing_refunds WHERE out_request_no = ?",
                (out_request_no,),
            ).fetchone()
            if row is None:
                raise ValueError("refund not found")
            record = RefundRecord.from_row(row)
            connection.execute(
                "UPDATE billing_refunds SET status = ?, fund_change = ?, raw = ?, "
                "updated_at = ? WHERE out_request_no = ?",
                (status, fund_change, raw, now, out_request_no),
            )
            if status == "SUCCESS":
                order = connection.execute(
                    "SELECT * FROM billing_orders WHERE out_trade_no = ?",
                    (record.out_trade_no,),
                ).fetchone()
                if order is not None and int(order["amount_cents"]) == record.amount_cents:
                    connection.execute(
                        "UPDATE billing_orders SET status = 'REFUNDED' WHERE out_trade_no = ?",
                        (record.out_trade_no,),
                    )
                    connection.execute(
                        "UPDATE billing_entitlements SET refunded_at = ?, expires_at = ? "
                        "WHERE order_no = ? AND refunded_at IS NULL",
                        (now, now, record.out_trade_no),
                    )
            connection.execute(
                "UPDATE billing_refund_requests SET status = ?, updated_at = ? "
                "WHERE out_request_no = ?",
                ("APPROVED" if status == "SUCCESS" else status, now, out_request_no),
            )
        updated = self.get_refund(out_request_no)
        if updated is None:
            raise RuntimeError("refund disappeared")
        return updated

    def get_refund(self, out_request_no: str) -> RefundRecord | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM billing_refunds WHERE out_request_no = ?",
                (out_request_no,),
            ).fetchone()
        return RefundRecord.from_row(row) if row is not None else None

    def create_refund_request(
        self,
        *,
        out_request_no: str,
        out_trade_no: str,
        user_id: str,
        reason: str,
        now: str,
    ) -> RefundRequest:
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM billing_refund_requests WHERE user_id = ? AND out_trade_no = ?",
                (user_id, out_trade_no),
            ).fetchone()
            if row is not None:
                return RefundRequest.from_row(row)
            connection.execute(
                "INSERT INTO billing_refund_requests(out_request_no, out_trade_no, user_id, "
                "reason, status, created_at, updated_at) VALUES(?, ?, ?, ?, 'REQUESTED', ?, ?)",
                (out_request_no, out_trade_no, user_id, reason, now, now),
            )
        request = self.get_refund_request(out_request_no)
        if request is None:
            raise RuntimeError("refund request insert did not persist")
        return request

    def get_refund_request(self, out_request_no: str) -> RefundRequest | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM billing_refund_requests WHERE out_request_no = ?",
                (out_request_no,),
            ).fetchone()
        return RefundRequest.from_row(row) if row is not None else None
