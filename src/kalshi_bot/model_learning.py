from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .performance_buckets import row_performance_bucket_keys


@dataclass(frozen=True)
class CalibrationAdjustment:
    asset: str
    samples: int
    avg_probability_yes: float
    actual_yes_rate: float
    bias: float
    adjustment: float
    brier_score: float
    log_loss: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "samples": self.samples,
            "avg_probability_yes": self.avg_probability_yes,
            "actual_yes_rate": self.actual_yes_rate,
            "bias": self.bias,
            "adjustment": self.adjustment,
            "brier_score": self.brier_score,
            "log_loss": self.log_loss,
        }


@dataclass(frozen=True)
class AssetPerformance:
    asset: str
    trades: int
    net_pnl_dollars: float
    avg_clv: float | None
    blocked: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "trades": self.trades,
            "net_pnl_dollars": self.net_pnl_dollars,
            "avg_clv": self.avg_clv,
            "blocked": self.blocked,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BucketPerformance:
    bucket_key: str
    trades: int
    net_pnl_dollars: float
    avg_clv: float | None
    blocked: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        group, _, bucket = self.bucket_key.partition(":")
        return {
            "bucket_key": self.bucket_key,
            "group": group,
            "bucket": bucket,
            "trades": self.trades,
            "net_pnl_dollars": self.net_pnl_dollars,
            "avg_clv": self.avg_clv,
            "blocked": self.blocked,
            "reason": self.reason,
        }


def load_asset_calibration(
    db_path: Path,
    *,
    mode: str = "live",
    min_samples: int = 20,
    window_trades: int = 200,
    strength: float = 0.35,
    max_adjustment: float = 0.10,
) -> dict[str, CalibrationAdjustment]:
    rows = _recent_final_settlement_rows(db_path, mode=mode, limit=window_trades)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        asset = str(row.get("asset") or "").upper()
        if asset:
            grouped.setdefault(asset, []).append(row)

    adjustments: dict[str, CalibrationAdjustment] = {}
    for asset, asset_rows in grouped.items():
        if len(asset_rows) < max(1, min_samples):
            continue
        stats = _yes_probability_stats(asset_rows)
        if stats is None:
            continue
        bias = stats["actual_yes_rate"] - stats["avg_probability_yes"]
        adjustment = _clamp(bias * strength, -abs(max_adjustment), abs(max_adjustment))
        adjustments[asset] = CalibrationAdjustment(
            asset=asset,
            samples=len(asset_rows),
            avg_probability_yes=round(stats["avg_probability_yes"], 4),
            actual_yes_rate=round(stats["actual_yes_rate"], 4),
            bias=round(bias, 4),
            adjustment=round(adjustment, 4),
            brier_score=round(stats["brier_score"], 4),
            log_loss=round(stats["log_loss"], 4),
        )
    return adjustments


def evaluate_asset_performance_guard(
    db_path: Path,
    *,
    mode: str = "live",
    min_trades: int = 20,
    window_trades: int = 100,
    min_net_pnl_dollars: float = 0.0,
    min_avg_clv: float = 0.0,
) -> dict[str, AssetPerformance]:
    rows = _recent_realized_rows(db_path, mode=mode, limit=window_trades)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        asset = str(row.get("asset") or "").upper()
        if asset:
            grouped.setdefault(asset, []).append(row)

    report: dict[str, AssetPerformance] = {}
    for asset, asset_rows in grouped.items():
        trades = len(asset_rows)
        net_pnl = round(sum(_float(row.get("net_pnl_dollars")) or 0.0 for row in asset_rows), 4)
        avg_clv = _avg_clv(asset_rows)
        blocked = False
        reasons: list[str] = []
        if trades >= max(1, min_trades):
            if net_pnl <= min_net_pnl_dollars:
                blocked = True
                reasons.append(f"net_pnl {net_pnl:.4f} <= {min_net_pnl_dollars:.4f}")
            if avg_clv is not None and avg_clv <= min_avg_clv:
                blocked = True
                reasons.append(f"avg_clv {avg_clv:.4f} <= {min_avg_clv:.4f}")
        else:
            reasons.append(f"only {trades} trades; need {min_trades}")
        report[asset] = AssetPerformance(
            asset=asset,
            trades=trades,
            net_pnl_dollars=net_pnl,
            avg_clv=avg_clv,
            blocked=blocked,
            reason="; ".join(reasons) if reasons else "passed",
        )
    return report


def evaluate_bucket_performance_guard(
    db_path: Path,
    *,
    mode: str = "live",
    min_trades: int = 20,
    window_trades: int = 100,
    min_net_pnl_dollars: float = 0.0,
    min_avg_clv: float = 0.0,
) -> dict[str, BucketPerformance]:
    rows = _recent_realized_rows(db_path, mode=mode, limit=window_trades)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        for bucket_key in row_performance_bucket_keys(row):
            grouped.setdefault(bucket_key, []).append(row)

    report: dict[str, BucketPerformance] = {}
    for bucket_key, bucket_rows in grouped.items():
        trades = len(bucket_rows)
        net_pnl = round(sum(_float(row.get("net_pnl_dollars")) or 0.0 for row in bucket_rows), 4)
        avg_clv = _avg_clv(bucket_rows)
        blocked = False
        reasons: list[str] = []
        if trades >= max(1, min_trades):
            if net_pnl <= min_net_pnl_dollars:
                blocked = True
                reasons.append(f"net_pnl {net_pnl:.4f} <= {min_net_pnl_dollars:.4f}")
            if avg_clv is not None and avg_clv <= min_avg_clv:
                blocked = True
                reasons.append(f"avg_clv {avg_clv:.4f} <= {min_avg_clv:.4f}")
        else:
            reasons.append(f"only {trades} trades; need {min_trades}")
        report[bucket_key] = BucketPerformance(
            bucket_key=bucket_key,
            trades=trades,
            net_pnl_dollars=net_pnl,
            avg_clv=avg_clv,
            blocked=blocked,
            reason="; ".join(reasons) if reasons else "passed",
        )
    return report


def _recent_final_settlement_rows(db_path: Path, *, mode: str, limit: int) -> list[dict[str, Any]]:
    return _query_rows(
        db_path,
        """
        SELECT o.id, o.mode, o.outcome, o.settlement_result, s.asset,
               s.estimated_probability, s.model_probability_yes
        FROM orders o
        JOIN signals s ON s.id=o.signal_id
        WHERE o.mode=?
          AND o.settlement_result IN ('yes', 'no')
        ORDER BY COALESCE(o.settled_at, o.updated_at, o.created_at) DESC, o.id DESC
        LIMIT ?
        """,
        (mode, max(1, int(limit))),
    )


def _recent_realized_rows(db_path: Path, *, mode: str, limit: int) -> list[dict[str, Any]]:
    return _query_rows(
        db_path,
        """
        SELECT o.id, o.mode, o.outcome, o.price, o.count, o.fill_count,
               o.average_fill_price, o.exit_average_fill_price, o.exit_fill_count,
               o.settlement_result, o.net_pnl_dollars, s.asset, s.spread,
               s.time_to_close_minutes
        FROM orders o
        JOIN signals s ON s.id=o.signal_id
        WHERE o.mode=?
          AND o.net_pnl_dollars IS NOT NULL
          AND (
            o.status IN ('paper_settled', 'live_closed', 'live_settled')
            OR COALESCE(o.exit_fill_count, 0) > 0
          )
        ORDER BY COALESCE(o.settled_at, o.updated_at, o.created_at) DESC, o.id DESC
        LIMIT ?
        """,
        (mode, max(1, int(limit))),
    )


def _query_rows(db_path: Path, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def _yes_probability_stats(rows: list[dict[str, Any]]) -> dict[str, float] | None:
    probabilities: list[float] = []
    actuals: list[float] = []
    for row in rows:
        probability = _probability_yes(row)
        settlement_result = row.get("settlement_result")
        if probability is None or settlement_result not in {"yes", "no"}:
            continue
        probabilities.append(_clamp(probability, 1e-6, 1.0 - 1e-6))
        actuals.append(1.0 if settlement_result == "yes" else 0.0)
    if not probabilities:
        return None
    brier = sum((probability - actual) ** 2 for probability, actual in zip(probabilities, actuals)) / len(probabilities)
    log_loss = sum(
        -(actual * math.log(probability) + (1.0 - actual) * math.log(1.0 - probability))
        for probability, actual in zip(probabilities, actuals)
    ) / len(probabilities)
    return {
        "avg_probability_yes": sum(probabilities) / len(probabilities),
        "actual_yes_rate": sum(actuals) / len(actuals),
        "brier_score": brier,
        "log_loss": log_loss,
    }


def _probability_yes(row: dict[str, Any]) -> float | None:
    model_probability_yes = _float(row.get("model_probability_yes"))
    if model_probability_yes is not None:
        return model_probability_yes
    estimated_probability = _float(row.get("estimated_probability"))
    outcome = row.get("outcome")
    if estimated_probability is None or outcome not in {"yes", "no"}:
        return None
    return estimated_probability if outcome == "yes" else 1.0 - estimated_probability


def _avg_clv(rows: list[dict[str, Any]]) -> float | None:
    total_value = 0.0
    total_count = 0.0
    for row in rows:
        entry_price = _float(row.get("average_fill_price")) or _float(row.get("price"))
        observed_value = _observed_value(row)
        count = _float(row.get("exit_fill_count")) or _float(row.get("fill_count")) or _float(row.get("count")) or 0.0
        if entry_price is None or observed_value is None or count <= 0:
            continue
        total_value += (observed_value - entry_price) * count
        total_count += count
    if total_count <= 0:
        return None
    return round(total_value / total_count, 4)


def _observed_value(row: dict[str, Any]) -> float | None:
    settlement_result = row.get("settlement_result")
    outcome = row.get("outcome")
    if settlement_result in {"yes", "no"} and outcome in {"yes", "no"}:
        return 1.0 if settlement_result == outcome else 0.0
    return _float(row.get("exit_average_fill_price"))


def _float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)
