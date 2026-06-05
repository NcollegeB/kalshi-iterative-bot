from __future__ import annotations

from types import SimpleNamespace

from kalshi_bot.cli import _live_correlation_rejections
from kalshi_bot.config import RiskConfig
from kalshi_bot.models import OutcomeSide, Signal, TradeMode


def _signal(ticker: str, edge: float, *, outcome: OutcomeSide = OutcomeSide.YES) -> Signal:
    return Signal.now(
        strategy="probability_file",
        ticker=ticker,
        market_title=ticker,
        outcome=outcome,
        estimated_probability=0.7,
        reference_price=0.2,
        edge=edge,
        reason="test",
        asset="ETH",
        spread=0.01,
        time_to_close_minutes=51,
    )


def test_live_correlation_guard_keeps_best_net_edge_in_bucket():
    config = SimpleNamespace(risk=RiskConfig(max_live_correlated_orders_per_scan=1))
    weaker = _signal("KXETHD-LOWER", 0.14)
    stronger = _signal("KXETHD-HIGHER", 0.2)

    rejections = _live_correlation_rejections([weaker, stronger], config, TradeMode.LIVE)

    assert len(rejections) == 1
    assert next(iter(rejections))[0] == "KXETHD-LOWER"
    assert "selected KXETHD-HIGHER:yes" in next(iter(rejections.values()))


def test_live_correlation_guard_does_not_apply_to_paper_mode():
    config = SimpleNamespace(risk=RiskConfig(max_live_correlated_orders_per_scan=1))

    rejections = _live_correlation_rejections([_signal("A", 0.14), _signal("B", 0.2)], config, TradeMode.PAPER)

    assert rejections == {}


def test_live_correlation_guard_respects_limit():
    config = SimpleNamespace(risk=RiskConfig(max_live_correlated_orders_per_scan=2))

    rejections = _live_correlation_rejections([_signal("A", 0.14), _signal("B", 0.2)], config, TradeMode.LIVE)

    assert rejections == {}
