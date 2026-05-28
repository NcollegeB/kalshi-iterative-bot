from pathlib import Path

from kalshi_bot.ledger import PaperLedger
from kalshi_bot.models import BookSide, OutcomeSide, ProposedOrder, Signal, TradeMode


def make_order(ticker: str, price: float) -> ProposedOrder:
    return ProposedOrder(
        ticker=ticker,
        book_side=BookSide.BID,
        outcome=OutcomeSide.YES,
        count=1,
        price=price,
        client_order_id=f"{ticker}-cid",
    )


def make_signal(ticker: str) -> Signal:
    return Signal.now(
        strategy="test",
        ticker=ticker,
        market_title="test",
        outcome=OutcomeSide.YES,
        estimated_probability=0.8,
        reference_price=0.5,
        edge=0.3,
        reason="test",
        asset="BTC",
        spread=0.02,
        time_to_close_minutes=15,
        annual_volatility=0.5,
        momentum_6h=0.01,
    )


def test_open_risk_can_be_scoped_by_mode(tmp_path: Path):
    ledger = PaperLedger(tmp_path / "ledger.sqlite3")
    paper_signal_id = ledger.record_signal(make_signal("PAPER"), TradeMode.PAPER, "approved", "ok")
    live_signal_id = ledger.record_signal(make_signal("LIVE"), TradeMode.LIVE, "approved", "ok")
    filled_live_signal_id = ledger.record_signal(make_signal("FILLED"), TradeMode.LIVE, "approved", "ok")
    ledger.record_order(paper_signal_id, make_order("PAPER", 0.5), TradeMode.PAPER, "paper_open")
    ledger.record_order(live_signal_id, make_order("LIVE", 0.7), TradeMode.LIVE, "live_submitted")
    ledger.record_order(filled_live_signal_id, make_order("FILLED", 0.9), TradeMode.LIVE, "live_executed")

    assert ledger.open_risk(TradeMode.PAPER) == 0.5
    assert ledger.open_risk(TradeMode.LIVE) == 1.6
    assert ledger.open_risk() == 2.1


def test_has_live_exposure_blocks_existing_live_ticker(tmp_path: Path):
    ledger = PaperLedger(tmp_path / "ledger.sqlite3")
    signal_id = ledger.record_signal(make_signal("LIVE"), TradeMode.LIVE, "approved", "ok")
    order_id = ledger.record_order(signal_id, make_order("LIVE", 0.7), TradeMode.LIVE, "live_executed")

    assert ledger.has_live_exposure("LIVE")
    assert ledger.has_live_exposure("LIVE", "yes")
    assert not ledger.has_live_exposure("OTHER")

    ledger.mark_exit_submitted(
        entry_order_id=order_id,
        exit_order_id="exit-id",
        exit_client_order_id="exit-cid",
        exit_price=0.8,
        exit_count=1,
        exit_fill_count=1,
        exit_remaining_count=0,
        take_profit_threshold=0.8,
        status="exit_executed",
    )
    assert not ledger.has_live_exposure("LIVE")


def test_mark_live_settled_clears_open_risk(tmp_path: Path):
    ledger = PaperLedger(tmp_path / "ledger.sqlite3")
    signal_id = ledger.record_signal(make_signal("LIVE"), TradeMode.LIVE, "approved", "ok")
    order_id = ledger.record_order(signal_id, make_order("LIVE", 0.7), TradeMode.LIVE, "live_executed")

    ledger.mark_live_settled(
        order_id=order_id,
        outcome_result="no",
        settlement_value=0.0,
        settled_at="2026-05-27T21:03:11Z",
        gross_pnl_dollars=-0.7,
        fee_estimate_dollars=0.0,
        net_pnl_dollars=-0.7,
    )

    assert ledger.open_risk(TradeMode.LIVE) == 0.0
    assert not ledger.has_live_exposure("LIVE")


def test_sync_live_order_from_exchange_updates_fill_state(tmp_path: Path):
    ledger = PaperLedger(tmp_path / "ledger.sqlite3")
    signal_id = ledger.record_signal(make_signal("LIVE"), TradeMode.LIVE, "approved", "ok")
    order_id = ledger.record_order(signal_id, make_order("LIVE", 0.7), TradeMode.LIVE, "live_submitted")

    ledger.sync_live_order_from_exchange(
        order_id=order_id,
        status="live_executed",
        fill_count=0.5,
        remaining_count=0.5,
        average_fill_price=0.7,
        fee_paid=0.01,
    )

    entries = ledger.list_live_entries_without_exit()
    assert len(entries) == 1
    assert entries[0]["fill_count"] == 0.5
    assert entries[0]["average_fill_price"] == 0.7


def test_list_unsettled_live_orders_preserves_zero_fill(tmp_path: Path):
    ledger = PaperLedger(tmp_path / "ledger.sqlite3")
    signal_id = ledger.record_signal(make_signal("LIVE"), TradeMode.LIVE, "approved", "ok")
    order_id = ledger.record_order(signal_id, make_order("LIVE", 0.7), TradeMode.LIVE, "live_submitted")

    ledger.sync_live_order_from_exchange(
        order_id=order_id,
        status="live_submitted",
        fill_count=0.0,
        remaining_count=1.0,
        average_fill_price=0.7,
        fee_paid=0.0,
    )

    orders = ledger.list_unsettled_live_orders()
    assert orders[0]["fill_count"] == 0.0


def test_open_risk_uses_remaining_live_exposure_after_partial_cancel(tmp_path: Path):
    ledger = PaperLedger(tmp_path / "ledger.sqlite3")
    signal_id = ledger.record_signal(make_signal("LIVE"), TradeMode.LIVE, "approved", "ok")
    order_id = ledger.record_order(signal_id, make_order("LIVE", 0.7), TradeMode.LIVE, "live_submitted")

    ledger.sync_live_order_from_exchange(
        order_id=order_id,
        status="live_executed",
        fill_count=0.25,
        remaining_count=0.0,
        average_fill_price=0.7,
        fee_paid=0.0,
    )

    assert ledger.open_risk(TradeMode.LIVE) == 0.175


def test_signal_metadata_is_recorded(tmp_path: Path):
    ledger = PaperLedger(tmp_path / "ledger.sqlite3")
    ledger.record_signal(make_signal("LIVE"), TradeMode.LIVE, "approved", "ok")

    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT asset, spread, time_to_close_minutes, annual_volatility, momentum_6h FROM signals"
        ).fetchone()

    assert row == ("BTC", 0.02, 15.0, 0.5, 0.01)


def test_exit_execution_records_realized_pnl_and_clears_risk(tmp_path: Path):
    ledger = PaperLedger(tmp_path / "ledger.sqlite3")
    signal_id = ledger.record_signal(make_signal("LIVE"), TradeMode.LIVE, "approved", "ok")
    order_id = ledger.record_order(signal_id, make_order("LIVE", 0.2), TradeMode.LIVE, "live_executed")
    ledger.sync_live_order_from_exchange(
        order_id=order_id,
        status="live_executed",
        fill_count=5.0,
        remaining_count=0.0,
        average_fill_price=0.2,
        fee_paid=0.01,
    )

    ledger.mark_exit_submitted(
        entry_order_id=order_id,
        exit_order_id="exit-id",
        exit_client_order_id="exit-cid",
        exit_price=0.4,
        exit_count=5.0,
        exit_fill_count=5.0,
        exit_remaining_count=0.0,
        take_profit_threshold=0.4,
        status="exit_executed",
        exit_average_fill_price=0.42,
        exit_fee_paid=0.02,
    )

    assert ledger.open_risk(TradeMode.LIVE) == 0.0
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, gross_pnl_dollars, fee_estimate_dollars, net_pnl_dollars FROM orders WHERE id=?",
            (order_id,),
        ).fetchone()
    assert row == ("live_closed", 1.1, 0.03, 1.07)
    assert ledger.realized_pnl_today(TradeMode.LIVE) == 1.07
