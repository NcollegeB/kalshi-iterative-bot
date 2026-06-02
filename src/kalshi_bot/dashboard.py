from __future__ import annotations

import ast
import csv
import html
import json
import math
import sqlite3
import subprocess
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .adaptive_risk import evaluate_adaptive_risk
from .config import AppConfig, DATA_DIR, PROJECT_ROOT
from .kalshi_client import KalshiApiError, KalshiClient
from .ledger import PaperLedger
from .models import TradeMode


LOG_PATH = PROJECT_ROOT / "logs" / "live-bot.log"
PROBABILITY_PATH = DATA_DIR / "probabilities.csv"


def serve_dashboard(config: AppConfig, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            route = urlparse(self.path).path
            if route == "/":
                self._send_html(DASHBOARD_HTML)
                return
            if route == "/api/snapshot":
                self._send_json(build_snapshot(config))
                return
            if route == "/api/style.css":
                self._send_css(DASHBOARD_CSS)
                return
            if route == "/api/app.js":
                self._send_js(DASHBOARD_JS)
                return
            self.send_error(404)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_html(self, content: str) -> None:
            self._send_bytes(content.encode("utf-8"), "text/html; charset=utf-8")

        def _send_css(self, content: str) -> None:
            self._send_bytes(content.encode("utf-8"), "text/css; charset=utf-8")

        def _send_js(self, content: str) -> None:
            self._send_bytes(content.encode("utf-8"), "application/javascript; charset=utf-8")

        def _send_json(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
            self._send_bytes(body, "application/json; charset=utf-8")

        def _send_bytes(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print({"dashboard_url": f"http://{host}:{port}", "read_only": True})
    server.serve_forever()


def build_snapshot(config: AppConfig) -> dict[str, Any]:
    ledger = PaperLedger(config.db_path)
    logs = parse_log_events(LOG_PATH, limit=300)
    adaptive_report = evaluate_adaptive_risk(
        config.risk,
        ledger.recent_realized_orders(
            TradeMode.LIVE,
            limit=max(config.risk.adaptive_window_trades, config.risk.adaptive_min_settled_trades),
        ),
    )
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "process": bot_process_status(),
        "risk": {
            **asdict(config.risk),
            "adaptive_multiplier": adaptive_report.multiplier,
            "effective_max_position_dollars": adaptive_report.effective_max_position_dollars,
            "effective_max_open_risk_dollars": adaptive_report.effective_max_open_risk_dollars,
            "effective_daily_loss_limit_dollars": adaptive_report.effective_daily_loss_limit_dollars,
            "live_open_risk_dollars": round(ledger.open_risk(TradeMode.LIVE), 4),
            "all_open_risk_dollars": round(ledger.open_risk(), 4),
        },
        "adaptive_risk": adaptive_report.to_dict(),
        "probability_file": probability_file_state(PROBABILITY_PATH),
        "loop": loop_summary(logs),
        "account": account_summary(config),
        "positions": live_positions(config.db_path),
        "orders": recent_orders(config.db_path),
        "signals": recent_signals(config.db_path),
        "performance": performance_summary(config.db_path),
        "performance_breakdown": performance_breakdown(config.db_path),
        "calibration": calibration_summary(config.db_path),
        "events": logs[-80:],
    }


def account_summary(config: AppConfig) -> dict[str, Any]:
    if not config.kalshi.has_credentials:
        return {"available": False, "error": "missing credentials"}
    try:
        balance = KalshiClient(config.kalshi, timeout_seconds=5).get_balance()
    except KalshiApiError as exc:
        return {"available": False, "error": str(exc)[:180]}
    cash = _float(balance.get("balance_dollars"))
    if cash is None and balance.get("balance") is not None:
        cash = (_float(balance.get("balance")) or 0.0) / 100.0
    portfolio_value = _float(balance.get("portfolio_value"))
    portfolio_value_dollars = round(portfolio_value / 100.0, 4) if portfolio_value is not None else None
    total = None
    if cash is not None or portfolio_value_dollars is not None:
        total = round((cash or 0.0) + (portfolio_value_dollars or 0.0), 4)
    return {
        "available": True,
        "cash_dollars": cash,
        "portfolio_value_dollars": portfolio_value_dollars,
        "total_equity_dollars": total,
        "updated_ts": balance.get("updated_ts"),
    }


def bot_process_status() -> dict[str, Any]:
    processes: list[dict[str, str]] = []
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid,etime,command"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception as exc:  # pragma: no cover - platform guard
        return {"running": False, "error": str(exc), "processes": []}
    for line in result.stdout.splitlines():
        if "kalshi-bot loop" not in line and "SCREEN -dmS kalshi-bot-live" not in line:
            continue
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        processes.append({"pid": parts[0], "elapsed": parts[1], "command": parts[2]})
    return {"running": any("kalshi-bot loop" in item["command"] for item in processes), "processes": processes}


def probability_file_state(path: Path) -> dict[str, Any]:
    rows = read_probability_rows(path)
    modified_at = path.stat().st_mtime if path.exists() else None
    return {
        "path": str(path),
        "modified_at": modified_at,
        "row_count": len(rows),
        "rows": rows[:25],
    }


def read_probability_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[dict[str, Any]] = []
        for row in reader:
            notes = row.get("notes", "")
            rows.append(
                {
                    "ticker": row.get("ticker", ""),
                    "estimated_probability": _float(row.get("estimated_probability")),
                    "notes": notes,
                    "metrics": parse_note_metrics(notes),
                }
            )
    return rows


def parse_note_metrics(notes: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    tokens = notes.split()
    if tokens and tokens[0].upper() in {"BTC", "ETH", "SOL", "XRP", "DOGE"}:
        metrics["asset"] = tokens[0].upper()
    for token in tokens:
        if "=" not in token:
            continue
        key, raw_value = token.split("=", 1)
        cleaned = raw_value.strip(",;")
        number = _float(cleaned)
        metrics[key] = number if number is not None else cleaned
    if "spread" not in metrics and "yes_ask" in metrics and "no_ask" in metrics:
        metrics["spread"] = round(float(metrics["yes_ask"]) + float(metrics["no_ask"]) - 1.0, 4)
    return metrics


def parse_log_events(path: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    lines = tail_lines(path, limit)
    events: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = ast.literal_eval(line)
        except (SyntaxError, ValueError):
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def tail_lines(path: Path, limit: int) -> list[str]:
    if limit <= 0 or not path.exists():
        return []
    chunk_size = 8192
    chunks: list[bytes] = []
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        newline_count = 0
        while position > 0 and newline_count <= limit:
            read_size = min(chunk_size, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")
    data = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
    return data.splitlines()[-limit:]


def loop_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    latest_iteration = _latest_with(events, "loop_iteration")
    latest_refresh = _latest_with(events, "assets")
    latest_scan = _latest_with(events, "markets_scanned")
    latest_take_profit = _latest_with(events, "entries_checked")
    latest_order_error = None
    for event in reversed(events):
        if event.get("order_errors") or event.get("kalshi_api_error"):
            latest_order_error = event
            break
    return {
        "iteration": latest_iteration,
        "refresh": latest_refresh,
        "scan": latest_scan,
        "take_profit": latest_take_profit,
        "latest_order_error": latest_order_error,
    }


def live_positions(db_path: Path) -> list[dict[str, Any]]:
    return _query_dicts(
        db_path,
        """
        SELECT id, ticker, outcome, count, price, status, fill_count, remaining_count,
               average_fill_price, average_fee_paid, exit_price, exit_average_fill_price,
               exit_fill_count, exit_fee_paid, exit_status, take_profit_threshold,
               max_loss_dollars, net_pnl_dollars
        FROM orders
        WHERE mode='live'
          AND status IN ('live_submitted', 'live_executed')
          AND COALESCE(exit_status, '') != 'exit_executed'
        ORDER BY id DESC
        """,
    )


def recent_orders(db_path: Path) -> list[dict[str, Any]]:
    return _query_dicts(
        db_path,
        """
        SELECT id, created_at, ticker, outcome, count, price, max_loss_dollars,
               mode, status, fill_count, remaining_count, average_fill_price,
               average_fee_paid, settlement_result, net_pnl_dollars, exit_status,
               exit_average_fill_price, exit_fee_paid
        FROM orders
        ORDER BY id DESC
        LIMIT 40
        """,
    )


def recent_signals(db_path: Path) -> list[dict[str, Any]]:
    rows = _query_dicts(
        db_path,
        """
        SELECT s.id, s.created_at, s.ticker, s.outcome, s.estimated_probability,
               s.reference_price, s.edge, s.mode, s.status, s.risk_reason, s.reason,
               s.asset, s.spread, s.time_to_close_minutes, s.annual_volatility,
               s.momentum_6h, s.model_probability_yes, s.kalshi_yes_ask, s.kalshi_no_ask,
               s.raw_probability_yes, s.raw_edge, o.id AS order_id, o.status AS order_status,
               o.settlement_result, o.net_pnl_dollars
        FROM signals s
        LEFT JOIN orders o ON o.signal_id=s.id
        ORDER BY s.id DESC
        LIMIT 80
        """,
    )
    for row in rows:
        row["metrics"] = parse_note_metrics(str(row.get("reason") or ""))
    return rows


def performance_summary(db_path: Path) -> dict[str, Any]:
    by_status = _query_dicts(
        db_path,
        """
        SELECT mode, status, COUNT(*) AS count, ROUND(COALESCE(SUM(max_loss_dollars), 0), 4) AS max_loss,
               ROUND(COALESCE(SUM(net_pnl_dollars), 0), 4) AS net_pnl
        FROM orders
        GROUP BY mode, status
        ORDER BY mode, status
        """,
    )
    settled = _query_dicts(
        db_path,
        """
        SELECT mode, COUNT(*) AS settled_count,
               ROUND(COALESCE(SUM(gross_pnl_dollars), 0), 4) AS gross_pnl,
               ROUND(COALESCE(SUM(fee_estimate_dollars), 0), 4) AS fees,
               ROUND(COALESCE(SUM(net_pnl_dollars), 0), 4) AS net_pnl
        FROM orders
        WHERE status IN ('paper_settled', 'live_settled')
        GROUP BY mode
        """,
    )
    live = _query_dicts(
        db_path,
        """
        SELECT COUNT(*) AS realized_count,
               ROUND(COALESCE(SUM(CASE WHEN net_pnl_dollars > 0 THEN 1 ELSE 0 END), 0), 0) AS wins,
               ROUND(COALESCE(SUM(max_loss_dollars), 0), 4) AS cost_basis,
               ROUND(COALESCE(SUM(gross_pnl_dollars), 0), 4) AS gross_pnl,
               ROUND(COALESCE(SUM(fee_estimate_dollars), 0), 4) AS fees,
               ROUND(COALESCE(SUM(net_pnl_dollars), 0), 4) AS net_pnl
        FROM orders
        WHERE mode='live'
          AND status IN ('live_closed', 'live_settled')
          AND net_pnl_dollars IS NOT NULL
        """,
    )
    live_summary = live[0] if live else {}
    cost_basis = float(live_summary.get("cost_basis") or 0)
    net_pnl = float(live_summary.get("net_pnl") or 0)
    live_summary["return_pct"] = round(net_pnl / cost_basis, 4) if cost_basis > 0 else None
    realized_count = int(live_summary.get("realized_count") or 0)
    wins = int(live_summary.get("wins") or 0)
    live_summary["win_rate"] = round(wins / realized_count, 4) if realized_count > 0 else None
    return {"by_status": by_status, "settled": settled, "live_realized": live_summary}


def performance_breakdown(db_path: Path, mode: str | None = None) -> dict[str, Any]:
    rows = _realized_performance_rows(db_path, mode=mode)
    for row in rows:
        row["asset_bucket"] = str(row.get("asset") or extract_asset(str(row.get("ticker") or "")))
        row["side_bucket"] = str(row.get("outcome") or "-")
        row["horizon_bucket"] = horizon_bucket(row.get("time_to_close_minutes"))
        row["spread_bucket"] = spread_bucket(row.get("spread"))
    segments = _group_performance(
        rows,
        "segment",
        lambda row: " / ".join(
            [
                row["asset_bucket"],
                row["side_bucket"],
                row["horizon_bucket"],
                row["spread_bucket"],
            ]
        ),
    )
    ranked_segments = sorted(segments, key=lambda row: (float(row["net_pnl"]), int(row["count"])), reverse=True)
    return {
        "mode_filter": mode or "all",
        "overall": _performance_metrics(rows),
        "by_mode": _group_performance(rows, "mode", lambda row: row.get("mode") or "-"),
        "by_asset": _group_performance(rows, "asset", lambda row: row["asset_bucket"]),
        "by_side": _group_performance(rows, "side", lambda row: row["side_bucket"]),
        "by_horizon": _group_performance(rows, "horizon", lambda row: row["horizon_bucket"]),
        "by_spread": _group_performance(rows, "spread", lambda row: row["spread_bucket"]),
        "top_segments": ranked_segments[:10],
        "bottom_segments": list(reversed(ranked_segments[-10:])),
    }


def _realized_performance_rows(db_path: Path, mode: str | None = None) -> list[dict[str, Any]]:
    mode_filter = ""
    params: list[Any] = []
    if mode is not None:
        mode_filter = "AND o.mode=?"
        params.append(mode)
    return _query_dicts(
        db_path,
        f"""
        SELECT o.id, o.ticker, o.mode, o.status, o.outcome, o.count, o.price,
               o.max_loss_dollars, o.gross_pnl_dollars, o.fee_estimate_dollars,
               o.net_pnl_dollars, o.settlement_result, o.exit_average_fill_price,
               o.average_fill_price, s.asset, s.estimated_probability,
               s.reference_price, s.edge, s.spread, s.time_to_close_minutes
        FROM orders o
        JOIN signals s ON s.id=o.signal_id
        WHERE o.net_pnl_dollars IS NOT NULL
          {mode_filter}
          AND (
            o.status IN ('paper_settled', 'live_closed', 'live_settled')
            OR COALESCE(o.exit_fill_count, 0) > 0
          )
        ORDER BY COALESCE(o.settled_at, o.updated_at, o.created_at), o.id
        """,
        params,
    )


def _group_performance(rows: list[dict[str, Any]], group: str, key_fn) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(key_fn(row) or "-"), []).append(row)
    report = [{"group": group, "bucket": bucket, **_performance_metrics(bucket_rows)} for bucket, bucket_rows in grouped.items()]
    report.sort(key=lambda row: (-int(row["count"]), str(row["bucket"])))
    return report


def _performance_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    final_rows = [row for row in rows if row.get("settlement_result") not in (None, "")]
    wins = sum(1 for row in final_rows if str(row.get("settlement_result")) == str(row.get("outcome")))
    max_loss = sum(_float(row.get("max_loss_dollars")) or 0.0 for row in rows)
    gross_pnl = sum(_float(row.get("gross_pnl_dollars")) or 0.0 for row in rows)
    fees = sum(_float(row.get("fee_estimate_dollars")) or 0.0 for row in rows)
    net_pnl = sum(_float(row.get("net_pnl_dollars")) or 0.0 for row in rows)
    calibration = _calibration_rows(
        [
            {
                "estimated_probability": row.get("estimated_probability"),
                "settlement_result": row.get("settlement_result"),
                "outcome": row.get("outcome"),
            }
            for row in final_rows
        ]
    )
    return {
        "count": count,
        "final_count": len(final_rows),
        "wins": wins,
        "win_rate": round(wins / len(final_rows), 4) if final_rows else None,
        "max_loss": round(max_loss, 4),
        "gross_pnl": round(gross_pnl, 4),
        "fees": round(fees, 4),
        "net_pnl": round(net_pnl, 4),
        "return_pct": round(net_pnl / max_loss, 4) if max_loss > 0 else None,
        "avg_edge": _average(rows, "edge"),
        "avg_probability": _average(rows, "estimated_probability"),
        "avg_price": _average(rows, "reference_price"),
        "avg_spread": _average(rows, "spread"),
        "avg_time_to_close_minutes": _average(rows, "time_to_close_minutes"),
        "brier_score": calibration["brier_score"],
        "log_loss": calibration["log_loss"],
    }


def calibration_summary(db_path: Path) -> dict[str, Any]:
    rows = _query_dicts(
        db_path,
        """
        SELECT s.asset, s.outcome, s.estimated_probability, o.settlement_result
        FROM signals s
        JOIN orders o ON o.signal_id=s.id
        WHERE o.status IN ('paper_settled', 'live_settled')
          AND o.settlement_result IS NOT NULL
        """,
    )
    overall = _calibration_rows(rows)
    by_asset = []
    assets = sorted({str(row.get("asset") or "-") for row in rows})
    for asset in assets:
        by_asset.append({"asset": asset, **_calibration_rows([row for row in rows if str(row.get("asset") or "-") == asset])})
    return {"overall": overall, "by_asset": by_asset}


def _calibration_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0, "brier_score": None, "log_loss": None, "actual_rate": None, "avg_probability": None}
    brier_total = 0.0
    log_total = 0.0
    actual_total = 0.0
    probability_total = 0.0
    for row in rows:
        probability = min(max(float(row["estimated_probability"]), 1e-6), 1 - 1e-6)
        actual = 1.0 if str(row.get("settlement_result")) == str(row.get("outcome")) else 0.0
        brier_total += (probability - actual) ** 2
        log_total += -(actual * math.log(probability) + (1.0 - actual) * math.log(1.0 - probability))
        actual_total += actual
        probability_total += probability
    count = len(rows)
    return {
        "count": count,
        "brier_score": round(brier_total / count, 4),
        "log_loss": round(log_total / count, 4),
        "actual_rate": round(actual_total / count, 4),
        "avg_probability": round(probability_total / count, 4),
    }


def _query_dicts(db_path: Path, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def _average(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [_float(row.get(key)) for row in rows]
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return round(sum(valid) / len(valid), 4)


def horizon_bucket(value: object) -> str:
    minutes = _float(value)
    if minutes is None:
        return "unknown"
    if minutes <= 10:
        return "<=10m"
    if minutes <= 30:
        return "10-30m"
    if minutes <= 60:
        return "30-60m"
    if minutes <= 240:
        return "1-4h"
    if minutes <= 1440:
        return "4-24h"
    return ">24h"


def spread_bucket(value: object) -> str:
    spread = _float(value)
    if spread is None:
        return "unknown"
    if spread <= 0.02:
        return "<=2c"
    if spread <= 0.05:
        return "2-5c"
    if spread <= 0.10:
        return "5-10c"
    return ">10c"


def extract_asset(ticker: str) -> str:
    if ticker.startswith("KXBTCD"):
        return "BTC"
    if ticker.startswith("KXETHD"):
        return "ETH"
    if ticker.startswith("KXSOLD"):
        return "SOL"
    if ticker.startswith("KXXRPD"):
        return "XRP"
    if ticker.startswith("KXDOGED"):
        return "DOGE"
    return "-"


def _latest_with(events: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    for event in reversed(events):
        if key in event:
            return event
    return None


def _float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Kalshi Bot Dashboard</title>
    <link rel="stylesheet" href="/api/style.css">
  </head>
  <body>
    <header class="topbar">
      <div>
        <h1>Kalshi Bot</h1>
        <p id="subtitle">Loading live state...</p>
      </div>
      <div class="status-strip">
        <span id="runBadge" class="badge">Checking</span>
        <span id="updatedAt"></span>
      </div>
    </header>

    <main>
      <section class="metric-grid">
        <div class="panel metric"><span>Kalshi Account Value</span><strong id="accountValue">-</strong></div>
        <div class="panel metric"><span>Kalshi Cash</span><strong id="accountCash">-</strong></div>
        <div class="panel metric"><span>Portfolio Value</span><strong id="portfolioValue">-</strong></div>
        <div class="panel metric"><span>Live Open Risk</span><strong id="liveRisk">-</strong></div>
        <div class="panel metric"><span>Live Realized Ledger PnL</span><strong id="livePnl">-</strong></div>
        <div class="panel metric"><span>Live Return</span><strong id="liveReturn">-</strong></div>
        <div class="panel metric"><span>Win Rate</span><strong id="winRate">-</strong></div>
        <div class="panel metric"><span>Brier Score</span><strong id="brierScore">-</strong></div>
        <div class="panel metric"><span>Log Loss</span><strong id="logLoss">-</strong></div>
        <div class="panel metric"><span>Risk Multiplier</span><strong id="riskMultiplier">-</strong></div>
        <div class="panel metric"><span>Max Position</span><strong id="maxPosition">-</strong></div>
        <div class="panel metric"><span>Max Open Risk</span><strong id="maxOpenRisk">-</strong></div>
        <div class="panel metric"><span>Model Candidates</span><strong id="candidateCount">-</strong></div>
        <div class="panel metric"><span>Last Scan Signals</span><strong id="lastSignals">-</strong></div>
      </section>

      <section class="split">
        <div class="panel">
          <div class="panel-head">
            <h2>Model Candidates</h2>
            <span id="probabilityModified"></span>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Ticker</th><th>Asset</th><th>Side</th><th>Edge</th><th>P(Yes)</th><th>Ask</th><th>Spread</th><th>Horizon</th><th>Vol</th><th>Mom</th></tr></thead>
              <tbody id="candidateRows"></tbody>
            </table>
          </div>
        </div>

        <div class="panel">
          <div class="panel-head">
            <h2>Live Positions</h2>
            <span id="positionCount"></span>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>ID</th><th>Ticker</th><th>Side</th><th>Fill</th><th>Price</th><th>Target</th><th>Status</th></tr></thead>
              <tbody id="positionRows"></tbody>
            </table>
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>Recent Decisions</h2>
          <span>signals, approvals, and exchange outcomes</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>ID</th><th>Time</th><th>Asset</th><th>Ticker</th><th>Side</th><th>Prob</th><th>Price</th><th>Edge</th><th>Spread</th><th>Final</th><th>PnL</th><th>Status</th><th>Order</th><th>Reason</th></tr></thead>
            <tbody id="signalRows"></tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>Performance Buckets</h2>
          <span>realized PnL and calibration by signal group</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Group</th><th>Bucket</th><th>Trades</th><th>Final</th><th>Net PnL</th><th>Return</th><th>Win</th><th>Brier</th><th>Log Loss</th><th>Avg Edge</th><th>Avg Spread</th><th>Avg Horizon</th></tr></thead>
            <tbody id="bucketRows"></tbody>
          </table>
        </div>
      </section>

      <section class="split">
        <div class="panel">
          <div class="panel-head">
            <h2>Loop Events</h2>
            <span>latest parsed log entries</span>
          </div>
          <div id="eventList" class="event-list"></div>
        </div>
        <div class="panel">
          <div class="panel-head">
            <h2>Risk And Performance</h2>
            <span>local ledger</span>
          </div>
          <div id="performanceList" class="kv-list"></div>
        </div>
      </section>
    </main>

    <script src="/api/app.js"></script>
  </body>
</html>
"""


DASHBOARD_CSS = """
:root {
  color-scheme: light;
  --bg: #f5f6f1;
  --panel: #ffffff;
  --ink: #1e2522;
  --muted: #68716d;
  --line: #dfe4dc;
  --green: #0f7b53;
  --red: #b63434;
  --amber: #9b6b12;
  --blue: #275a89;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 14px;
}

.topbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 22px 14px;
  border-bottom: 1px solid var(--line);
  background: #fbfcf8;
  position: sticky;
  top: 0;
  z-index: 2;
}
h1, h2, p { margin: 0; }
h1 { font-size: 24px; font-weight: 720; }
h2 { font-size: 15px; font-weight: 700; }
#subtitle { margin-top: 4px; color: var(--muted); }
.status-strip { display: flex; align-items: center; gap: 12px; color: var(--muted); white-space: nowrap; }
.badge {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 4px 10px;
  border-radius: 999px;
  background: #ecefe9;
  color: var(--muted);
  font-weight: 700;
}
.badge.good { background: #dff2e9; color: var(--green); }
.badge.bad { background: #f7e2df; color: var(--red); }

main { padding: 18px 22px 28px; display: grid; gap: 16px; }
.metric-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(130px, 1fr));
  gap: 12px;
}
.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
}
.metric { padding: 14px; }
.metric span { display: block; color: var(--muted); font-size: 12px; }
.metric strong { display: block; margin-top: 7px; font-size: 21px; letter-spacing: 0; }
.split {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
}
.panel-head {
  min-height: 45px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--line);
}
.panel-head span { color: var(--muted); font-size: 12px; text-align: right; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; min-width: 720px; }
th, td {
  padding: 9px 10px;
  border-bottom: 1px solid #edf0e9;
  text-align: left;
  vertical-align: top;
  white-space: nowrap;
}
th { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0; background: #fafbf7; }
td.reason { white-space: normal; min-width: 260px; color: var(--muted); }
.num { font-variant-numeric: tabular-nums; }
.side-yes { color: var(--green); font-weight: 700; }
.side-no { color: var(--blue); font-weight: 700; }
.warn { color: var(--amber); font-weight: 700; }
.error { color: var(--red); font-weight: 700; }
.event-list, .kv-list {
  padding: 10px 14px 14px;
  display: grid;
  gap: 9px;
  max-height: 460px;
  overflow: auto;
}
.event, .kv {
  border: 1px solid #edf0e9;
  border-radius: 6px;
  padding: 9px 10px;
  background: #fcfdf9;
}
.event code {
  display: block;
  margin-top: 6px;
  white-space: pre-wrap;
  word-break: break-word;
  color: #38413d;
  font-size: 12px;
}
.empty {
  padding: 18px 14px;
  color: var(--muted);
}
@media (max-width: 1100px) {
  .metric-grid { grid-template-columns: repeat(3, minmax(130px, 1fr)); }
  .split { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  .topbar { align-items: flex-start; flex-direction: column; }
  main { padding: 14px; }
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric strong { font-size: 18px; }
}
"""


DASHBOARD_JS = """
const $ = (id) => document.getElementById(id);

function money(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `$${Number(value).toFixed(2)}`;
}

function pct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function num(value, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(digits);
}

function shortTime(value) {
  if (!value) return "-";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function setRows(tbody, rows, render, emptyText) {
  tbody.innerHTML = "";
  if (!rows || rows.length === 0) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="empty" colspan="12">${emptyText}</td>`;
    tbody.appendChild(tr);
    return;
  }
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = render(row);
    tbody.appendChild(tr);
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderSnapshot(data) {
  const running = data.process?.running;
  $("runBadge").textContent = running ? "Running" : "Stopped";
  $("runBadge").className = `badge ${running ? "good" : "bad"}`;
  $("updatedAt").textContent = `Updated ${shortTime(data.generated_at)}`;
  const proc = data.process?.processes?.find((p) => p.command.includes("kalshi-bot loop"));
  $("subtitle").textContent = proc ? `PID ${proc.pid}, running ${proc.elapsed}, 60 second loop` : "No loop process detected";

  const account = data.account || {};
  $("accountValue").textContent = account.available ? money(account.total_equity_dollars) : "-";
  $("accountCash").textContent = account.available ? money(account.cash_dollars) : "-";
  $("portfolioValue").textContent = account.available ? money(account.portfolio_value_dollars) : "-";
  $("liveRisk").textContent = money(data.risk.live_open_risk_dollars);
  const livePerf = data.performance?.live_realized || {};
  const calibration = data.calibration?.overall || {};
  $("livePnl").textContent = money(livePerf.net_pnl);
  $("liveReturn").textContent = pct(livePerf.return_pct);
  $("winRate").textContent = pct(livePerf.win_rate);
  $("brierScore").textContent = num(calibration.brier_score, 4);
  $("logLoss").textContent = num(calibration.log_loss, 4);
  $("riskMultiplier").textContent = `${num(data.risk.adaptive_multiplier || 1, 2)}x`;
  $("maxPosition").textContent = money(data.risk.effective_max_position_dollars ?? data.risk.max_position_dollars);
  $("maxOpenRisk").textContent = money(data.risk.effective_max_open_risk_dollars ?? data.risk.max_open_risk_dollars);
  $("candidateCount").textContent = data.probability_file.row_count;
  $("lastSignals").textContent = data.loop?.scan?.signals ?? "-";
  $("probabilityModified").textContent = data.probability_file.modified_at ? `not orders · file ${shortTime(data.probability_file.modified_at)}` : "not orders · no file";
  $("positionCount").textContent = `${data.positions.length} open`;

  setRows($("candidateRows"), data.probability_file.rows, (row) => {
    const m = row.metrics || {};
    const side = m.side || "-";
    const ask = side === "no" ? m.no_ask : m.yes_ask;
    const spread = m.spread ?? ((m.yes_ask !== undefined && m.no_ask !== undefined) ? Number(m.yes_ask) + Number(m.no_ask) - 1 : null);
    return `<td>${escapeHtml(row.ticker)}</td>
      <td>${escapeHtml(m.asset || extractAsset(row.ticker))}</td>
      <td class="side-${escapeHtml(side)}">${escapeHtml(side)}</td>
      <td class="num">${num(m.edge)}</td>
      <td class="num">${pct(row.estimated_probability)}</td>
      <td class="num">${num(ask, 2)}</td>
      <td class="num">${num(spread, 2)}</td>
      <td class="num">${num(m.horizon_min, 1)}m</td>
      <td class="num">${pct(m.annual_vol)}</td>
      <td class="num">${pct(m.momentum_6h)}</td>`;
  }, "No current model candidates passed the edge filters.");

  setRows($("positionRows"), data.positions, (row) => {
    const fill = row.fill_count ?? row.count;
    const target = row.take_profit_threshold || row.exit_price || (Number(row.price || 0) * 2);
    return `<td class="num">${row.id}</td>
      <td>${escapeHtml(row.ticker)}</td>
      <td class="side-${escapeHtml(row.outcome)}">${escapeHtml(row.outcome)}</td>
      <td class="num">${num(fill, 2)} / ${num(row.count, 2)}</td>
      <td class="num">${num(row.average_fill_price || row.price, 2)}</td>
      <td class="num">${num(target, 2)}</td>
      <td>${escapeHtml(row.status)}${row.exit_status ? ` / ${escapeHtml(row.exit_status)}` : ""}</td>`;
  }, "No open live positions.");

  setRows($("signalRows"), data.signals.slice(0, 30), (row) => {
    const order = row.order_id ? `#${row.order_id} ${row.order_status || ""}` : "none";
    const statusClass = row.status === "approved" && !row.order_id ? "warn" : "";
    const finalResult = row.settlement_result || "-";
    return `<td class="num">${row.id}</td>
      <td>${shortTime(row.created_at)}</td>
      <td>${escapeHtml(row.asset || extractAsset(row.ticker))}</td>
      <td>${escapeHtml(row.ticker)}</td>
      <td class="side-${escapeHtml(row.outcome)}">${escapeHtml(row.outcome)}</td>
      <td class="num">${pct(row.estimated_probability)}</td>
      <td class="num">${num(row.reference_price, 2)}</td>
      <td class="num">${num(row.edge)}</td>
      <td class="num">${num(row.spread, 2)}</td>
      <td>${escapeHtml(finalResult)}</td>
      <td class="num">${money(row.net_pnl_dollars)}</td>
      <td class="${statusClass}">${escapeHtml(row.status)}</td>
      <td>${escapeHtml(order)}</td>
      <td class="reason">${escapeHtml(row.risk_reason || row.reason || "")}</td>`;
  }, "No signal records yet.");

  const buckets = data.performance_breakdown || {};
  const bucketRows = [
    ...(buckets.by_mode || []),
    ...(buckets.by_asset || []),
    ...(buckets.by_side || []),
    ...(buckets.by_horizon || []),
    ...(buckets.by_spread || [])
  ];
  setRows($("bucketRows"), bucketRows, (row) => {
    const horizon = row.avg_time_to_close_minutes === null || row.avg_time_to_close_minutes === undefined
      ? "-"
      : `${num(row.avg_time_to_close_minutes, 1)}m`;
    const pnlClass = Number(row.net_pnl || 0) < 0 ? "error" : "";
    return `<td>${escapeHtml(row.group)}</td>
      <td>${escapeHtml(row.bucket)}</td>
      <td class="num">${row.count}</td>
      <td class="num">${row.final_count}</td>
      <td class="num ${pnlClass}">${money(row.net_pnl)}</td>
      <td class="num">${pct(row.return_pct)}</td>
      <td class="num">${pct(row.win_rate)}</td>
      <td class="num">${num(row.brier_score, 4)}</td>
      <td class="num">${num(row.log_loss, 4)}</td>
      <td class="num">${num(row.avg_edge, 4)}</td>
      <td class="num">${num(row.avg_spread, 4)}</td>
      <td class="num">${horizon}</td>`;
  }, "No realized trades yet.");

  const events = [...(data.events || [])].reverse().slice(0, 18);
  $("eventList").innerHTML = events.length ? events.map((event) => {
    const label = event.loop_iteration ? `Loop ${event.loop_iteration}` :
      event.markets_scanned !== undefined ? "Scan" :
      event.entries_checked !== undefined ? "Take profit" :
      event.assets ? "Refresh" : "Event";
    const cls = event.order_errors || event.kalshi_api_error ? "event error" : "event";
    return `<div class="${cls}"><strong>${escapeHtml(label)}</strong><code>${escapeHtml(JSON.stringify(event, null, 2))}</code></div>`;
  }).join("") : `<div class="empty">No parsed log events.</div>`;

  const perfRows = data.performance.by_status || [];
  const calRows = data.calibration?.by_asset || [];
  const adaptive = data.adaptive_risk || {};
  $("performanceList").innerHTML = [
    `<div class="kv"><strong>Kalshi account</strong><br>${account.available ? `${money(account.total_equity_dollars)} total, ${money(account.cash_dollars)} cash, ${money(account.portfolio_value_dollars)} portfolio` : `Unavailable: ${escapeHtml(account.error || "")}`}</div>`,
    `<div class="kv"><strong>Risk</strong><br>Live ${money(data.risk.live_open_risk_dollars)} / ${money(data.risk.effective_max_open_risk_dollars ?? data.risk.max_open_risk_dollars)} open risk</div>`,
    `<div class="kv"><strong>Adaptive sizing</strong><br>${num(adaptive.multiplier || 1, 2)}x, ${escapeHtml(adaptive.direction || "neutral")}: ${escapeHtml(adaptive.reason || "")}<br>${adaptive.final_result_count || 0}/${adaptive.window_trades || 0} final results, PnL ${money(adaptive.net_pnl_dollars)}, CLV ${num(adaptive.avg_clv, 4)}, drawdown ${money(adaptive.max_drawdown_dollars)}</div>`,
    `<div class="kv"><strong>Live realized ledger</strong><br>${livePerf.realized_count || 0} closed/settled, ${money(livePerf.net_pnl)} net, ${pct(livePerf.return_pct)} return, ${pct(livePerf.win_rate)} win rate</div>`,
    `<div class="kv"><strong>Calibration</strong><br>${calibration.count || 0} settled predictions, Brier ${num(calibration.brier_score, 4)}, log loss ${num(calibration.log_loss, 4)}, actual ${pct(calibration.actual_rate)}, avg prob ${pct(calibration.avg_probability)}</div>`,
    ...calRows.map((row) => `<div class="kv"><strong>${escapeHtml(row.asset)} calibration</strong><br>${row.count} settled, Brier ${num(row.brier_score, 4)}, log loss ${num(row.log_loss, 4)}, actual ${pct(row.actual_rate)}</div>`),
    ...perfRows.map((row) => `<div class="kv"><strong>${escapeHtml(row.mode)} ${escapeHtml(row.status)}</strong><br>${row.count} orders, ${money(row.max_loss)} max loss, ${money(row.net_pnl)} net PnL</div>`)
  ].join("");
}

function extractAsset(ticker) {
  if (ticker.startsWith("KXBTCD")) return "BTC";
  if (ticker.startsWith("KXETHD")) return "ETH";
  if (ticker.startsWith("KXSOLD")) return "SOL";
  if (ticker.startsWith("KXXRPD")) return "XRP";
  if (ticker.startsWith("KXDOGED")) return "DOGE";
  return "-";
}

async function refresh() {
  try {
    const response = await fetch("/api/snapshot", { cache: "no-store" });
    renderSnapshot(await response.json());
  } catch (error) {
    $("runBadge").textContent = "UI Error";
    $("runBadge").className = "badge bad";
    $("subtitle").textContent = error.message;
  }
}

refresh();
setInterval(refresh, 5000);
"""
