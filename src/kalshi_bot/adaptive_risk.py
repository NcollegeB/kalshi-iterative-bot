from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .config import RiskConfig


@dataclass(frozen=True)
class AdaptiveRiskReport:
    enabled: bool
    multiplier: float
    direction: str
    reason: str
    window_trades: int
    realized_count: int
    final_result_count: int
    net_pnl_dollars: float | None
    return_on_risk: float | None
    avg_clv: float | None
    max_drawdown_dollars: float | None
    brier_score: float | None
    log_loss: float | None
    effective_max_position_dollars: float
    effective_max_open_risk_dollars: float
    effective_daily_loss_limit_dollars: float
    checks: dict[str, bool | None]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_adaptive_risk(config: RiskConfig, rows: Sequence[Mapping[str, Any]]) -> AdaptiveRiskReport:
    window_trades = max(1, int(config.adaptive_window_trades))
    bounded_rows = list(rows)[:window_trades]
    if not config.adaptive_risk_enabled:
        return _report(
            config,
            multiplier=1.0,
            direction="disabled",
            reason="adaptive risk disabled",
            window_trades=window_trades,
        )

    metrics = _calculate_metrics(bounded_rows)
    checks = _checks(config, metrics)
    multiplier = 1.0
    direction = "neutral"
    reason = f"waiting for {config.adaptive_min_settled_trades} final results"

    drawdown_bad = checks["drawdown_ok"] is False
    enough_final = checks["enough_final_results"] is True
    performance_checks = (
        checks["positive_pnl"],
        checks["positive_clv"],
        checks["brier_ok"],
        checks["log_loss_ok"],
        checks["drawdown_ok"],
    )

    if drawdown_bad:
        direction = "down"
        multiplier = max(config.adaptive_min_multiplier, 1.0 - config.adaptive_step_down)
        reason = "drawdown over adaptive limit"
    elif enough_final and all(value is True for value in performance_checks):
        direction = "up"
        multiplier = min(config.adaptive_max_multiplier, 1.0 + config.adaptive_step_up)
        reason = "rolling window passed PnL, CLV, calibration, and drawdown checks"
    elif enough_final and any(value is False for value in performance_checks):
        direction = "down"
        multiplier = max(config.adaptive_min_multiplier, 1.0 - config.adaptive_step_down)
        reason = "rolling window failed one or more adaptive checks"

    return _report(
        config,
        multiplier=multiplier,
        direction=direction,
        reason=reason,
        window_trades=window_trades,
        realized_count=metrics["realized_count"],
        final_result_count=metrics["final_result_count"],
        net_pnl_dollars=metrics["net_pnl_dollars"],
        return_on_risk=metrics["return_on_risk"],
        avg_clv=metrics["avg_clv"],
        max_drawdown_dollars=metrics["max_drawdown_dollars"],
        brier_score=metrics["brier_score"],
        log_loss=metrics["log_loss"],
        checks=checks,
    )


def effective_risk_limits(config: RiskConfig, multiplier: float) -> dict[str, float]:
    multiplier = max(0.0, float(multiplier))
    return {
        "max_position_dollars": round(config.max_position_dollars * multiplier, 4),
        "max_open_risk_dollars": round(config.max_open_risk_dollars * multiplier, 4),
        "daily_loss_limit_dollars": round(config.daily_loss_limit_dollars * multiplier, 4),
        "max_bankroll_fraction_per_trade": min(1.0, config.max_bankroll_fraction_per_trade * multiplier),
        "kelly_fraction": min(1.0, config.kelly_fraction * multiplier),
    }


def _calculate_metrics(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    realized_count = len(rows)
    net_pnl = round(sum(_float(row.get("net_pnl_dollars")) or 0.0 for row in rows), 4)
    risk_basis = sum(_float(row.get("max_loss_dollars")) or 0.0 for row in rows)
    final_rows = [row for row in rows if _actual_win(row) is not None]
    brier_score, log_loss = _calibration_metrics(final_rows)
    avg_clv = _avg_clv(rows)
    max_drawdown = _max_drawdown(rows)
    return {
        "realized_count": realized_count,
        "final_result_count": len(final_rows),
        "net_pnl_dollars": net_pnl if realized_count else None,
        "return_on_risk": round(net_pnl / risk_basis, 4) if risk_basis > 0 else None,
        "avg_clv": avg_clv,
        "max_drawdown_dollars": max_drawdown,
        "brier_score": brier_score,
        "log_loss": log_loss,
    }


def _checks(config: RiskConfig, metrics: Mapping[str, Any]) -> dict[str, bool | None]:
    realized_count = int(metrics["realized_count"])
    final_count = int(metrics["final_result_count"])
    enough_final = final_count >= max(1, config.adaptive_min_settled_trades)
    net_pnl = metrics["net_pnl_dollars"]
    avg_clv = metrics["avg_clv"]
    max_drawdown = metrics["max_drawdown_dollars"]
    brier_score = metrics["brier_score"]
    log_loss = metrics["log_loss"]
    return {
        "enough_final_results": enough_final,
        "positive_pnl": (net_pnl > config.adaptive_min_net_pnl_dollars) if realized_count else None,
        "positive_clv": (avg_clv > config.adaptive_min_avg_clv) if avg_clv is not None else None,
        "brier_ok": (brier_score <= config.adaptive_max_brier_score) if brier_score is not None else None,
        "log_loss_ok": (log_loss <= config.adaptive_max_log_loss) if log_loss is not None else None,
        "drawdown_ok": (max_drawdown <= config.adaptive_max_drawdown_dollars) if max_drawdown is not None else None,
    }


def _calibration_metrics(rows: list[Mapping[str, Any]]) -> tuple[float | None, float | None]:
    if not rows:
        return None, None
    brier_total = 0.0
    log_total = 0.0
    count = 0
    for row in rows:
        probability = _float(row.get("estimated_probability"))
        actual = _actual_win(row)
        if probability is None or actual is None:
            continue
        probability = min(max(probability, 1e-6), 1.0 - 1e-6)
        brier_total += (probability - actual) ** 2
        log_total += -(actual * math.log(probability) + (1.0 - actual) * math.log(1.0 - probability))
        count += 1
    if count == 0:
        return None, None
    return round(brier_total / count, 4), round(log_total / count, 4)


def _avg_clv(rows: list[Mapping[str, Any]]) -> float | None:
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


def _observed_value(row: Mapping[str, Any]) -> float | None:
    actual = _actual_win(row)
    if actual is not None:
        return actual
    return _float(row.get("exit_average_fill_price"))


def _max_drawdown(rows: list[Mapping[str, Any]]) -> float | None:
    if not rows:
        return None
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for row in sorted(rows, key=lambda item: (str(item.get("realized_at") or ""), int(_float(item.get("id")) or 0))):
        equity += _float(row.get("net_pnl_dollars")) or 0.0
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return round(max_drawdown, 4)


def _actual_win(row: Mapping[str, Any]) -> float | None:
    settlement_result = row.get("settlement_result")
    outcome = row.get("outcome")
    if settlement_result in (None, "") or outcome in (None, ""):
        return None
    return 1.0 if str(settlement_result).lower() == str(outcome).lower() else 0.0


def _report(
    config: RiskConfig,
    *,
    multiplier: float,
    direction: str,
    reason: str,
    window_trades: int,
    realized_count: int = 0,
    final_result_count: int = 0,
    net_pnl_dollars: float | None = None,
    return_on_risk: float | None = None,
    avg_clv: float | None = None,
    max_drawdown_dollars: float | None = None,
    brier_score: float | None = None,
    log_loss: float | None = None,
    checks: dict[str, bool | None] | None = None,
) -> AdaptiveRiskReport:
    limits = effective_risk_limits(config, multiplier)
    return AdaptiveRiskReport(
        enabled=config.adaptive_risk_enabled,
        multiplier=round(multiplier, 4),
        direction=direction,
        reason=reason,
        window_trades=window_trades,
        realized_count=realized_count,
        final_result_count=final_result_count,
        net_pnl_dollars=net_pnl_dollars,
        return_on_risk=return_on_risk,
        avg_clv=avg_clv,
        max_drawdown_dollars=max_drawdown_dollars,
        brier_score=brier_score,
        log_loss=log_loss,
        effective_max_position_dollars=limits["max_position_dollars"],
        effective_max_open_risk_dollars=limits["max_open_risk_dollars"],
        effective_daily_loss_limit_dollars=limits["daily_loss_limit_dollars"],
        checks=checks or {
            "enough_final_results": None,
            "positive_pnl": None,
            "positive_clv": None,
            "brier_ok": None,
            "log_loss_ok": None,
            "drawdown_ok": None,
        },
    )


def _float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
