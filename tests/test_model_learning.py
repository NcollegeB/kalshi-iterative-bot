from pathlib import Path

from kalshi_bot.ledger import PaperLedger
from kalshi_bot.model_learning import evaluate_asset_performance_guard, load_asset_calibration
from kalshi_bot.models import BookSide, OutcomeSide, ProposedOrder, Signal, TradeMode


def signal(ticker: str, *, asset: str = "BTC", probability_yes: float = 0.8) -> Signal:
    return Signal.now(
        strategy="test",
        ticker=ticker,
        market_title="test",
        outcome=OutcomeSide.YES,
        estimated_probability=probability_yes,
        reference_price=0.4,
        edge=probability_yes - 0.4,
        reason="test",
        asset=asset,
        model_probability_yes=probability_yes,
    )


def order(ticker: str) -> ProposedOrder:
    return ProposedOrder(
        ticker=ticker,
        book_side=BookSide.BID,
        outcome=OutcomeSide.YES,
        count=1,
        price=0.4,
        client_order_id=f"{ticker}-cid",
    )


def settled_live_order(
    ledger: PaperLedger,
    *,
    ticker: str,
    asset: str = "BTC",
    probability_yes: float = 0.8,
    settlement_result: str = "no",
    net_pnl: float = -0.4,
) -> None:
    signal_id = ledger.record_signal(signal(ticker, asset=asset, probability_yes=probability_yes), TradeMode.LIVE, "approved", "ok")
    order_id = ledger.record_order(signal_id, order(ticker), TradeMode.LIVE, "live_executed")
    ledger.mark_live_settled(
        order_id=order_id,
        outcome_result=settlement_result,
        settlement_value=1.0 if settlement_result == "yes" else 0.0,
        settled_at="2026-05-28T17:00:00Z",
        gross_pnl_dollars=net_pnl,
        fee_estimate_dollars=0.0,
        net_pnl_dollars=net_pnl,
    )


def test_load_asset_calibration_computes_capped_bias_adjustment(tmp_path: Path):
    ledger = PaperLedger(tmp_path / "ledger.sqlite3")
    settled_live_order(ledger, ticker="BTC1", probability_yes=0.8, settlement_result="no")
    settled_live_order(ledger, ticker="BTC2", probability_yes=0.6, settlement_result="no")

    adjustments = load_asset_calibration(
        ledger.path,
        min_samples=2,
        strength=0.5,
        max_adjustment=0.2,
    )

    assert adjustments["BTC"].samples == 2
    assert adjustments["BTC"].avg_probability_yes == 0.7
    assert adjustments["BTC"].actual_yes_rate == 0.0
    assert adjustments["BTC"].adjustment == -0.2


def test_performance_guard_blocks_assets_with_negative_recent_pnl_and_clv(tmp_path: Path):
    ledger = PaperLedger(tmp_path / "ledger.sqlite3")
    settled_live_order(ledger, ticker="BTC1", probability_yes=0.8, settlement_result="no", net_pnl=-0.4)
    settled_live_order(ledger, ticker="BTC2", probability_yes=0.6, settlement_result="no", net_pnl=-0.4)
    settled_live_order(ledger, ticker="ETH1", asset="ETH", probability_yes=0.6, settlement_result="yes", net_pnl=0.6)

    report = evaluate_asset_performance_guard(ledger.path, min_trades=2)

    assert report["BTC"].blocked
    assert "net_pnl" in report["BTC"].reason
    assert report["ETH"].blocked is False
    assert "only 1 trades" in report["ETH"].reason
