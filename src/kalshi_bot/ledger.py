from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .models import ProposedOrder, Signal, TradeMode
from .simulation import SignalSample


class PaperLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def record_signal(self, signal: Signal, mode: TradeMode, status: str, risk_reason: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO signals (
                    created_at, strategy, ticker, market_title, outcome, estimated_probability,
                    reference_price, edge, reason, mode, status, risk_reason,
                    asset, model_probability_yes, kalshi_yes_ask, kalshi_no_ask, spread,
                    time_to_close_minutes, annual_volatility, momentum_6h, raw_probability_yes,
                    raw_edge
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.created_at.isoformat(),
                    signal.strategy,
                    signal.ticker,
                    signal.market_title,
                    signal.outcome.value,
                    signal.estimated_probability,
                    signal.reference_price,
                    signal.edge,
                    signal.reason,
                    mode.value,
                    status,
                    risk_reason,
                    signal.asset,
                    signal.model_probability_yes,
                    signal.kalshi_yes_ask,
                    signal.kalshi_no_ask,
                    signal.spread,
                    signal.time_to_close_minutes,
                    signal.annual_volatility,
                    signal.momentum_6h,
                    signal.raw_probability_yes,
                    signal.raw_edge,
                ),
            )
            return int(cursor.lastrowid)

    def record_order(
        self,
        signal_id: int,
        order: ProposedOrder,
        mode: TradeMode,
        status: str,
        exchange_response: dict[str, Any] | None = None,
    ) -> int:
        response = exchange_response or {}
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO orders (
                    signal_id, created_at, ticker, book_side, outcome, count, price,
                    max_loss_dollars, client_order_id, mode, status, exchange_order_id,
                    fill_count, remaining_count, average_fill_price, average_fee_paid,
                    updated_at
                )
                VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    signal_id,
                    order.ticker,
                    order.book_side.value,
                    order.outcome.value,
                    order.count,
                    order.price,
                    round(order.count * order.price, 4),
                    order.client_order_id,
                    mode.value,
                    status,
                    response.get("order_id"),
                    _optional_float(response.get("fill_count")),
                    _optional_float(response.get("remaining_count")),
                    _optional_float(response.get("average_fill_price")),
                    _optional_float(response.get("average_fee_paid")),
                ),
            )
            return int(cursor.lastrowid)

    def open_risk(self, mode: TradeMode | None = None) -> float:
        statuses = {
            TradeMode.PAPER: ("paper_open",),
            TradeMode.DEMO: ("demo_submitted", "demo_executed"),
            TradeMode.LIVE: ("live_submitted", "live_executed"),
        }
        selected_statuses = statuses.get(
            mode,
            ("paper_open", "demo_submitted", "demo_executed", "live_submitted", "live_executed"),
        )
        placeholders = ",".join("?" for _ in selected_statuses)
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                SELECT COALESCE(SUM(
                    CASE
                        WHEN mode='live' AND fill_count IS NOT NULL THEN
                            MAX(COALESCE(fill_count, 0) - COALESCE(exit_fill_count, 0), 0)
                            * COALESCE(average_fill_price, price)
                            + COALESCE(remaining_count, 0) * price
                        ELSE max_loss_dollars
                    END
                ), 0)
                FROM orders
                WHERE status IN ({placeholders})
                  AND COALESCE(exit_status, '') != 'exit_executed'
                """,
                selected_statuses,
            )
            return float(cursor.fetchone()[0])

    def realized_pnl_today(self, mode: TradeMode | None = None) -> float:
        mode_filter = ""
        params: list[str] = []
        if mode is not None:
            mode_filter = "AND mode=?"
            params.append(mode.value)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT COALESCE(SUM(net_pnl_dollars), 0)
                FROM orders
                WHERE net_pnl_dollars IS NOT NULL
                  {mode_filter}
                  AND (
                    status IN ('paper_settled', 'live_closed', 'live_settled')
                    OR COALESCE(exit_fill_count, 0) > 0
                  )
                  AND date(COALESCE(settled_at, updated_at, created_at), 'localtime') = date('now', 'localtime')
                """,
                params,
            ).fetchone()
        return float(row[0])

    def has_live_exposure(self, ticker: str, outcome: str | None = None) -> bool:
        params: list[str] = [ticker]
        outcome_filter = ""
        if outcome is not None:
            outcome_filter = "AND outcome=?"
            params.append(outcome)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM orders
                WHERE mode='live'
                  AND ticker=?
                  {outcome_filter}
                  AND status IN ('live_submitted', 'live_executed')
                  AND COALESCE(exit_status, '') != 'exit_executed'
                """,
                params,
            ).fetchone()
        return int(row[0]) > 0

    def summary(self) -> dict[str, float | int]:
        with self._connect() as conn:
            signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            open_risk = self.open_risk()
            settled = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(net_pnl_dollars), 0) FROM orders WHERE status='paper_settled'"
            ).fetchone()
            live_realized = conn.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(net_pnl_dollars), 0)
                FROM orders
                WHERE mode='live'
                  AND status IN ('live_closed', 'live_settled')
                  AND net_pnl_dollars IS NOT NULL
                """
            ).fetchone()
        return {
            "signals": int(signals),
            "orders": int(orders),
            "open_risk": float(open_risk),
            "paper_settled_orders": int(settled[0]),
            "paper_net_pnl": float(settled[1]),
            "live_realized_orders": int(live_realized[0]),
            "live_realized_net_pnl": float(live_realized[1]),
        }

    def list_unsettled_paper_orders(self, limit: int | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT id, signal_id, ticker, outcome, count, price, max_loss_dollars, status
            FROM orders
            WHERE mode='paper' AND status='paper_open'
            ORDER BY id
        """
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": int(row[0]),
                "signal_id": int(row[1]),
                "ticker": str(row[2]),
                "outcome": str(row[3]),
                "count": float(row[4]),
                "price": float(row[5]),
                "max_loss_dollars": float(row[6]),
                "status": str(row[7]),
            }
            for row in rows
        ]

    def list_live_entries_without_exit(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, ticker, outcome, count, price, max_loss_dollars, exchange_order_id,
                       fill_count, average_fill_price, status, exit_fill_count, average_fee_paid
                FROM orders
                WHERE mode='live'
                  AND status IN ('live_submitted', 'live_executed')
                  AND COALESCE(fill_count, 0) > COALESCE(exit_fill_count, 0)
                ORDER BY id
                """
            ).fetchall()
        return [
            {
                "id": int(row[0]),
                "ticker": str(row[1]),
                "outcome": str(row[2]),
                "count": float(row[3]),
                "price": float(row[4]),
                "max_loss_dollars": float(row[5]),
                "exchange_order_id": row[6],
                "fill_count": round((_optional_float(row[7]) or float(row[3])) - (_optional_float(row[10]) or 0.0), 4),
                "total_fill_count": _optional_float(row[7]) or float(row[3]),
                "exit_fill_count": _optional_float(row[10]) or 0.0,
                "average_fee_paid": _optional_float(row[11]) or 0.0,
                "average_fill_price": _optional_float(row[8]) or float(row[4]),
                "status": str(row[9]),
            }
            for row in rows
        ]

    def list_unsettled_live_orders(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, ticker, outcome, count, price, max_loss_dollars, exchange_order_id,
                       fill_count, average_fill_price, average_fee_paid, status
                FROM orders
                WHERE mode='live'
                  AND status IN ('live_submitted', 'live_executed')
                ORDER BY id
                """
            ).fetchall()
        return [
            {
                "id": int(row[0]),
                "ticker": str(row[1]),
                "outcome": str(row[2]),
                "count": float(row[3]),
                "price": float(row[4]),
                "max_loss_dollars": float(row[5]),
                "exchange_order_id": row[6],
                "fill_count": _optional_float(row[7]) if _optional_float(row[7]) is not None else float(row[3]),
                "average_fill_price": _optional_float(row[8]) or float(row[4]),
                "average_fee_paid": _optional_float(row[9]) or 0.0,
                "status": str(row[10]),
            }
            for row in rows
        ]

    def sync_live_order_from_exchange(
        self,
        *,
        order_id: int,
        status: str,
        fill_count: float,
        remaining_count: float,
        average_fill_price: float | None,
        fee_paid: float | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET status=?,
                    fill_count=?,
                    remaining_count=?,
                    average_fill_price=?,
                    average_fee_paid=?,
                    updated_at=datetime('now')
                WHERE id=?
                """,
                (
                    status,
                    fill_count,
                    remaining_count,
                    average_fill_price,
                    fee_paid,
                    order_id,
                ),
            )

    def mark_exit_submitted(
        self,
        *,
        entry_order_id: int,
        exit_order_id: str,
        exit_client_order_id: str,
        exit_price: float,
        exit_count: float,
        exit_fill_count: float,
        exit_remaining_count: float,
        take_profit_threshold: float,
        status: str,
        exit_average_fill_price: float | None = None,
        exit_fee_paid: float = 0.0,
    ) -> None:
        with self._connect() as conn:
            entry = conn.execute(
                """
                SELECT count, price, fill_count, average_fill_price, average_fee_paid,
                       COALESCE(exit_fill_count, 0), COALESCE(gross_pnl_dollars, 0),
                       COALESCE(fee_estimate_dollars, 0), COALESCE(net_pnl_dollars, 0)
                FROM orders
                WHERE id=?
                """,
                (entry_order_id,),
            ).fetchone()
            realized_gross = 0.0
            realized_fees = 0.0
            realized_net = 0.0
            if entry and exit_fill_count > 0:
                entry_count = _optional_float(entry[2]) or float(entry[0])
                if entry_count > 0:
                    entry_price = _optional_float(entry[3]) or float(entry[1])
                    entry_fee_paid = _optional_float(entry[4]) or 0.0
                    already_exited = _optional_float(entry[5]) or 0.0
                    exit_price_actual = exit_average_fill_price or exit_price
                    entry_fee_remaining = max(
                        entry_fee_paid - ((already_exited / entry_count) * entry_fee_paid),
                        0.0,
                    )
                    entry_fee_alloc = min(entry_fee_remaining, (exit_fill_count / entry_count) * entry_fee_paid)
                    realized_gross = round(exit_fill_count * (exit_price_actual - entry_price), 4)
                    realized_fees = round(entry_fee_alloc + exit_fee_paid, 4)
                    realized_net = round(realized_gross - realized_fees, 4)

            conn.execute(
                """
                UPDATE orders
                SET exit_order_id=?,
                    exit_client_order_id=?,
                    exit_price=?,
                    exit_average_fill_price=?,
                    exit_count=?,
                    exit_fill_count=COALESCE(exit_fill_count, 0) + ?,
                    exit_remaining_count=?,
                    exit_fee_paid=COALESCE(exit_fee_paid, 0) + ?,
                    take_profit_threshold=?,
                    exit_status=?,
                    gross_pnl_dollars=COALESCE(gross_pnl_dollars, 0) + ?,
                    fee_estimate_dollars=COALESCE(fee_estimate_dollars, 0) + ?,
                    net_pnl_dollars=COALESCE(net_pnl_dollars, 0) + ?,
                    updated_at=datetime('now'),
                    status=CASE
                        WHEN ?='exit_executed' THEN 'live_closed'
                        ELSE status
                    END
                WHERE id=?
                """,
                (
                    exit_order_id,
                    exit_client_order_id,
                    exit_price,
                    exit_average_fill_price or exit_price,
                    exit_count,
                    exit_fill_count,
                    exit_remaining_count,
                    exit_fee_paid,
                    take_profit_threshold,
                    status,
                    realized_gross,
                    realized_fees,
                    realized_net,
                    status,
                    entry_order_id,
                ),
            )

    def mark_live_settled(
        self,
        *,
        order_id: int,
        outcome_result: str,
        settlement_value: float,
        settled_at: str | None,
        gross_pnl_dollars: float,
        fee_estimate_dollars: float,
        net_pnl_dollars: float,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET status='live_settled',
                    settlement_result=?,
                    settlement_value=?,
                    settled_at=?,
                    gross_pnl_dollars=?,
                    fee_estimate_dollars=?,
                    net_pnl_dollars=?,
                    updated_at=datetime('now')
                WHERE id=?
                """,
                (
                    outcome_result,
                    settlement_value,
                    settled_at,
                    gross_pnl_dollars,
                    fee_estimate_dollars,
                    net_pnl_dollars,
                    order_id,
                ),
            )

    def mark_paper_settled(
        self,
        *,
        order_id: int,
        outcome_result: str,
        settlement_value: float,
        settled_at: str | None,
        gross_pnl_dollars: float,
        fee_estimate_dollars: float,
        net_pnl_dollars: float,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET status='paper_settled',
                    settlement_result=?,
                    settlement_value=?,
                    settled_at=?,
                    gross_pnl_dollars=?,
                    fee_estimate_dollars=?,
                    net_pnl_dollars=?,
                    updated_at=datetime('now')
                WHERE id=?
                """,
                (
                    outcome_result,
                    settlement_value,
                    settled_at,
                    gross_pnl_dollars,
                    fee_estimate_dollars,
                    net_pnl_dollars,
                    order_id,
                ),
            )

    def settlement_summary(self) -> dict[str, float | int]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*),
                    COALESCE(SUM(gross_pnl_dollars), 0),
                    COALESCE(SUM(fee_estimate_dollars), 0),
                    COALESCE(SUM(net_pnl_dollars), 0),
                    COALESCE(AVG(net_pnl_dollars), 0)
                FROM orders
                WHERE status='paper_settled'
                """
            ).fetchone()
        return {
            "paper_settled_orders": int(row[0]),
            "paper_gross_pnl": float(row[1]),
            "paper_fee_estimate": float(row[2]),
            "paper_net_pnl": float(row[3]),
            "paper_avg_net_pnl": float(row[4]),
        }

    def load_signal_samples(self, limit: int | None = None) -> list[SignalSample]:
        sql = """
            SELECT id, ticker, market_title, outcome, estimated_probability, reference_price, edge
            FROM signals
            ORDER BY id
        """
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            SignalSample(
                signal_id=int(row[0]),
                ticker=str(row[1]),
                market_title=str(row[2]),
                outcome=str(row[3]),
                estimated_probability=float(row[4]),
                reference_price=float(row[5]),
                edge=float(row[6]),
            )
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    market_title TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    estimated_probability REAL NOT NULL,
                    reference_price REAL NOT NULL,
                    edge REAL NOT NULL,
                    reason TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    risk_reason TEXT NOT NULL
                )
                """
            )
            self._ensure_columns(
                conn,
                "signals",
                {
                    "asset": "TEXT",
                    "model_probability_yes": "REAL",
                    "kalshi_yes_ask": "REAL",
                    "kalshi_no_ask": "REAL",
                    "spread": "REAL",
                    "time_to_close_minutes": "REAL",
                    "annual_volatility": "REAL",
                    "momentum_6h": "REAL",
                    "raw_probability_yes": "REAL",
                    "raw_edge": "REAL",
                },
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    book_side TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    count REAL NOT NULL,
                    price REAL NOT NULL,
                    max_loss_dollars REAL NOT NULL,
                    client_order_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    FOREIGN KEY(signal_id) REFERENCES signals(id)
                )
                """
            )
            self._ensure_columns(
                conn,
                "orders",
                {
                    "exchange_order_id": "TEXT",
                    "fill_count": "REAL",
                    "remaining_count": "REAL",
                    "average_fill_price": "REAL",
                    "average_fee_paid": "REAL",
                    "settlement_result": "TEXT",
                    "settlement_value": "REAL",
                    "settled_at": "TEXT",
                    "gross_pnl_dollars": "REAL",
                    "fee_estimate_dollars": "REAL",
                    "net_pnl_dollars": "REAL",
                    "exit_order_id": "TEXT",
                    "exit_client_order_id": "TEXT",
                    "exit_price": "REAL",
                    "exit_average_fill_price": "REAL",
                    "exit_count": "REAL",
                    "exit_fill_count": "REAL",
                    "exit_remaining_count": "REAL",
                    "exit_fee_paid": "REAL",
                    "take_profit_threshold": "REAL",
                    "exit_status": "TEXT",
                    "updated_at": "TEXT",
                },
            )

    def _ensure_columns(self, conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
