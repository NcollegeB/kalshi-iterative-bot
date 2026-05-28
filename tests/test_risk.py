from kalshi_bot.config import RiskConfig
from kalshi_bot.models import OutcomeSide, Signal
from kalshi_bot.risk import PortfolioState, RiskManager


def make_signal(edge=0.1, price=0.5):
    return Signal.now(
        strategy="test",
        ticker="TEST",
        market_title="Test market",
        outcome=OutcomeSide.YES,
        estimated_probability=price + edge,
        reference_price=price,
        edge=edge,
        reason="test",
    )


def test_risk_approves_and_sizes_with_position_cap():
    risk = RiskManager(RiskConfig(max_position_dollars=1.0, min_edge_dollars=0.05, kelly_fraction=0.0))
    decision = risk.evaluate(make_signal(edge=0.1, price=0.25), PortfolioState(20.0, 0.0))
    assert decision.approved
    assert decision.count == 4.0
    assert decision.max_loss_dollars == 1.0


def test_risk_caps_size_with_fractional_kelly():
    risk = RiskManager(
        RiskConfig(
            bankroll_dollars=20.0,
            max_position_dollars=2.0,
            min_edge_dollars=0.05,
            max_bankroll_fraction_per_trade=0.10,
            kelly_fraction=0.25,
        )
    )
    decision = risk.evaluate(make_signal(edge=0.1, price=0.25), PortfolioState(20.0, 0.0))

    assert decision.approved
    assert decision.count == 2.66
    assert decision.max_loss_dollars == 0.665


def test_risk_rejects_low_edge():
    risk = RiskManager(RiskConfig(min_edge_dollars=0.08))
    decision = risk.evaluate(make_signal(edge=0.02), PortfolioState(20.0, 0.0))
    assert not decision.approved
    assert "below minimum" in decision.reason


def test_risk_rejects_when_open_risk_full():
    risk = RiskManager(RiskConfig(max_open_risk_dollars=5.0))
    decision = risk.evaluate(make_signal(edge=0.2), PortfolioState(20.0, 5.0))
    assert not decision.approved
    assert "max open risk" in decision.reason


def test_risk_rejects_when_daily_loss_limit_is_hit():
    risk = RiskManager(RiskConfig(daily_loss_limit_dollars=2.0))
    decision = risk.evaluate(make_signal(edge=0.2), PortfolioState(20.0, 0.0, realized_pnl_today_dollars=-2.01))
    assert not decision.approved
    assert "daily loss" in decision.reason
