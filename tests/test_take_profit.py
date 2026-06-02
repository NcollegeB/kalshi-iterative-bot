from kalshi_bot.cli import (
    _calculate_live_settlement,
    _event_order_response_exit_price,
    _event_order_response_fee_paid,
    _exit_order_from_entry,
    _exit_status_from_response,
    _exchange_fill_price,
    _fill_stats_for_outcome,
    _local_status_from_exchange_order,
    _order_status_from_response,
    _target_exit_price,
)
from kalshi_bot.models import BookSide, OutcomeSide, ProposedOrder, TopOfBook, TradeMode


class FakeClient:
    def __init__(self, top):
        self.top = top

    def get_orderbook(self, ticker):
        return self.top

    def make_client_order_id(self, prefix="kb"):
        return f"{prefix}-cid"


def test_target_exit_price_uses_profit_percent():
    assert _target_exit_price(entry_price=0.06, profit_pct=100, min_profit_cents=0) == 0.12


def test_target_exit_price_respects_min_profit_cents():
    assert _target_exit_price(entry_price=0.06, profit_pct=10, min_profit_cents=5) == 0.11


def test_order_status_marks_filled_live_response_as_executed():
    assert _order_status_from_response(TradeMode.LIVE, {"fill_count": "1.00"}) == "live_executed"
    assert _order_status_from_response(TradeMode.LIVE, {"fill_count": "0.00"}) == "live_submitted"


def test_exit_status_marks_full_ioc_fill_executed():
    assert _exit_status_from_response(2.0, {"fill_count": "2.00", "remaining_count": "0.00"}) == "exit_executed"
    assert _exit_status_from_response(2.0, {"fill_count": "1.00", "remaining_count": "1.00"}) == "exit_partial"
    assert _exit_status_from_response(2.0, {"fill_count": "0.00", "remaining_count": "2.00"}) == "exit_submitted"


def test_live_settlement_uses_actual_settlement_value():
    result = _calculate_live_settlement(
        {
            "outcome": "yes",
            "fill_count": 25.0,
            "average_fill_price": 0.06,
            "average_fee_paid": 0.0,
        },
        {"value": 0, "market_result": "no", "settled_time": "2026-05-27T21:03:11Z", "fee_cost": "0.000000"},
    )
    assert result["outcome_result"] == "no"
    assert result["net_pnl_dollars"] == -1.5


def test_local_status_marks_partial_resting_fill_executed():
    assert _local_status_from_exchange_order({"status": "resting"}, 9.0, 16.0) == "live_executed"
    assert _local_status_from_exchange_order({"status": "resting"}, 0.0, 16.0) == "live_submitted"
    assert _local_status_from_exchange_order({"status": "canceled"}, 0.0, 0.0) == "live_closed"


def test_exchange_fill_price_uses_outcome_side():
    order = {"yes_price_dollars": "0.0800", "no_price_dollars": "0.9200"}
    assert _exchange_fill_price("yes", order) == 0.08
    assert _exchange_fill_price("no", order) == 0.92


def test_event_order_response_exit_price_converts_no_exit_average_price():
    order = ProposedOrder(
        ticker="T",
        book_side=BookSide.BID,
        outcome=OutcomeSide.NO,
        count=25,
        price=0.14,
        client_order_id="tp-cid",
        reduce_only=True,
    )

    assert _event_order_response_exit_price(order, {"average_fill_price": "0.3700"}) == 0.63
    assert _event_order_response_exit_price(order, {"no_price_dollars": "0.1400"}) == 0.14


def test_event_order_response_fee_paid_scales_average_fee_by_fill_count():
    assert _event_order_response_fee_paid({"average_fee_paid": "0.0164"}, 25.0) == 0.41
    assert _event_order_response_fee_paid({"maker_fees_dollars": "0.0100", "taker_fees_dollars": "0.0200"}, 25.0) == 0.03


def test_fill_stats_for_outcome_uses_outcome_side_price_and_total_fees():
    fills = [
        {
            "count_fp": "25.00",
            "fee_cost": "0.410000",
            "yes_price_dollars": "0.3700",
            "no_price_dollars": "0.6300",
        }
    ]

    assert _fill_stats_for_outcome("no", fills) == {"count": 25.0, "average_price": 0.63, "fee_paid": 0.41}
    assert _fill_stats_for_outcome("yes", fills) == {"count": 25.0, "average_price": 0.37, "fee_paid": 0.41}


def test_exit_order_waits_until_yes_bid_reaches_target():
    client = FakeClient(TopOfBook(yes_bid=0.11, yes_bid_size=1, no_bid=0.88, no_bid_size=1))
    entry = {"ticker": "T", "fill_count": 2}

    assert _exit_order_from_entry(client, entry, OutcomeSide.YES, 0.12) is None

    client = FakeClient(TopOfBook(yes_bid=0.12, yes_bid_size=1, no_bid=0.87, no_bid_size=1))
    order = _exit_order_from_entry(client, entry, OutcomeSide.YES, 0.12)
    assert order is not None
    assert order.reduce_only is True
    assert order.time_in_force == "immediate_or_cancel"
    assert order.post_only is False


def test_exit_order_waits_until_no_bid_reaches_target():
    client = FakeClient(TopOfBook(yes_bid=0.80, yes_bid_size=1, no_bid=0.13, no_bid_size=1))
    entry = {"ticker": "T", "fill_count": 2}

    assert _exit_order_from_entry(client, entry, OutcomeSide.NO, 0.14) is None

    client = FakeClient(TopOfBook(yes_bid=0.79, yes_bid_size=1, no_bid=0.14, no_bid_size=1))
    order = _exit_order_from_entry(client, entry, OutcomeSide.NO, 0.14)
    assert order is not None
    assert order.reduce_only is True
    assert order.time_in_force == "immediate_or_cancel"
