from kalshi_bot.adaptive_risk import evaluate_adaptive_risk
from kalshi_bot.config import RiskConfig


def row(
    *,
    probability: float,
    outcome: str,
    settlement_result: str | None,
    price: float,
    net_pnl: float,
    max_loss: float = 1.0,
    realized_at: str = "2026-05-28T17:00:00Z",
):
    return {
        "id": 1,
        "realized_at": realized_at,
        "estimated_probability": probability,
        "outcome": outcome,
        "settlement_result": settlement_result,
        "price": price,
        "count": 1.0,
        "fill_count": 1.0,
        "max_loss_dollars": max_loss,
        "net_pnl_dollars": net_pnl,
    }


def test_adaptive_risk_waits_for_enough_final_results():
    config = RiskConfig(adaptive_risk_enabled=True, adaptive_min_settled_trades=3, adaptive_window_trades=5)
    rows = [
        row(probability=0.7, outcome="yes", settlement_result="yes", price=0.5, net_pnl=0.5),
        row(probability=0.8, outcome="yes", settlement_result="yes", price=0.5, net_pnl=0.5),
    ]

    report = evaluate_adaptive_risk(config, rows)

    assert report.multiplier == 1.0
    assert report.direction == "neutral"
    assert report.final_result_count == 2


def test_adaptive_risk_steps_up_when_window_passes_checks():
    config = RiskConfig(
        adaptive_risk_enabled=True,
        adaptive_min_settled_trades=3,
        adaptive_window_trades=5,
        adaptive_step_up=0.15,
        adaptive_max_multiplier=1.25,
    )
    rows = [
        row(probability=0.8, outcome="yes", settlement_result="yes", price=0.4, net_pnl=0.6),
        row(probability=0.75, outcome="no", settlement_result="no", price=0.45, net_pnl=0.55),
        row(probability=0.7, outcome="yes", settlement_result="yes", price=0.5, net_pnl=0.5),
    ]

    report = evaluate_adaptive_risk(config, rows)

    assert report.multiplier == 1.15
    assert report.direction == "up"
    assert report.checks["positive_pnl"] is True
    assert report.checks["positive_clv"] is True


def test_adaptive_risk_steps_down_when_calibration_fails():
    config = RiskConfig(
        adaptive_risk_enabled=True,
        adaptive_min_settled_trades=3,
        adaptive_window_trades=5,
        adaptive_step_down=0.25,
        adaptive_min_multiplier=0.5,
    )
    rows = [
        row(probability=0.9, outcome="yes", settlement_result="no", price=0.8, net_pnl=-0.8),
        row(probability=0.85, outcome="yes", settlement_result="no", price=0.8, net_pnl=-0.8),
        row(probability=0.8, outcome="yes", settlement_result="no", price=0.8, net_pnl=-0.8),
    ]

    report = evaluate_adaptive_risk(config, rows)

    assert report.multiplier == 0.75
    assert report.direction == "down"
    assert report.checks["brier_ok"] is False
    assert report.checks["log_loss_ok"] is False


def test_adaptive_risk_steps_down_on_drawdown_before_full_sample():
    config = RiskConfig(
        adaptive_risk_enabled=True,
        adaptive_min_settled_trades=20,
        adaptive_window_trades=20,
        adaptive_max_drawdown_dollars=1.0,
    )
    rows = [
        row(
            probability=0.6,
            outcome="yes",
            settlement_result=None,
            price=0.5,
            net_pnl=-1.5,
            realized_at="2026-05-28T17:01:00Z",
        )
    ]

    report = evaluate_adaptive_risk(config, rows)

    assert report.multiplier == 0.75
    assert report.direction == "down"
    assert report.checks["drawdown_ok"] is False
