from kalshi_bot.config import RiskConfig
from kalshi_bot.models import OutcomeSide, Signal
from kalshi_bot.risk import PortfolioState, RiskManager


def make_signal(edge=0.1, price=0.5, asset="BTC", spread=0.01, time_to_close_minutes=20):
    return Signal.now(
        strategy="test",
        ticker="TEST",
        market_title="Test market",
        outcome=OutcomeSide.YES,
        estimated_probability=price + edge,
        reference_price=price,
        edge=edge,
        reason="test",
        asset=asset,
        spread=spread,
        time_to_close_minutes=time_to_close_minutes,
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
    assert decision.count == 2.13
    assert decision.max_loss_dollars == 0.5325
    assert decision.net_edge_after_fees == 0.08


def test_risk_rejects_edge_that_is_not_profitable_after_fees():
    risk = RiskManager(RiskConfig(min_edge_dollars=0.08))
    decision = risk.evaluate(make_signal(edge=0.09, price=0.5), PortfolioState(20.0, 0.0))

    assert not decision.approved
    assert "net_edge_after_fees" in decision.reason
    assert decision.raw_edge_dollars == 0.09
    assert decision.fee_haircut_dollars == 0.02


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


def test_risk_multiplier_scales_budget_caps():
    risk = RiskManager(
        RiskConfig(
            max_position_dollars=4.0,
            max_open_risk_dollars=10.0,
            max_bankroll_fraction_per_trade=0.20,
            min_edge_dollars=0.05,
            kelly_fraction=0.0,
        ),
        risk_multiplier=1.25,
    )
    decision = risk.evaluate(make_signal(edge=0.3, price=0.25), PortfolioState(20.0, 0.0))

    assert decision.approved
    assert decision.count == 20.0
    assert decision.max_loss_dollars == 5.0


def test_risk_rejects_disallowed_asset():
    risk = RiskManager(RiskConfig(allowed_assets=("BTC", "ETH")))
    decision = risk.evaluate(make_signal(asset="XRP"), PortfolioState(20.0, 0.0))
    assert not decision.approved
    assert "outside allowed assets" in decision.reason


def test_risk_rejects_blocked_asset():
    risk = RiskManager(RiskConfig(allowed_assets=("BTC", "ETH")), blocked_assets=("BTC",))
    decision = risk.evaluate(make_signal(asset="BTC"), PortfolioState(20.0, 0.0))
    assert not decision.approved
    assert "performance guard" in decision.reason


def test_risk_rejects_blocked_bucket():
    risk = RiskManager(
        RiskConfig(allowed_assets=("BTC", "ETH")),
        blocked_buckets={"asset_side:BTC:yes": "net_pnl -1.0000 <= 0.0000"},
    )
    decision = risk.evaluate(make_signal(asset="BTC"), PortfolioState(20.0, 0.0))

    assert not decision.approved
    assert "bucket asset_side:BTC:yes" in decision.reason


def test_risk_rejects_wide_spread():
    risk = RiskManager(RiskConfig(max_spread_dollars=0.02))
    decision = risk.evaluate(make_signal(spread=0.05), PortfolioState(20.0, 0.0))
    assert not decision.approved
    assert "spread" in decision.reason


def test_risk_rejects_time_to_close_outside_window():
    risk = RiskManager(RiskConfig(min_time_to_close_minutes=10.0, max_time_to_close_minutes=60.0))

    too_soon = risk.evaluate(make_signal(time_to_close_minutes=5.0), PortfolioState(20.0, 0.0))
    too_late = risk.evaluate(make_signal(time_to_close_minutes=90.0), PortfolioState(20.0, 0.0))

    assert not too_soon.approved
    assert "below minimum" in too_soon.reason
    assert not too_late.approved
    assert "above maximum" in too_late.reason
