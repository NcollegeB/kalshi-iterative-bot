from kalshi_bot.kalshi_client import event_order_payload
from kalshi_bot.models import BookSide, OutcomeSide, ProposedOrder


def test_yes_order_uses_yes_book_bid_price():
    order = ProposedOrder(
        ticker="TEST",
        book_side=BookSide.BID,
        outcome=OutcomeSide.YES,
        count=2,
        price=0.42,
        client_order_id="cid",
    )
    payload = event_order_payload(order)
    assert payload["side"] == "bid"
    assert payload["price"] == "0.4200"


def test_no_order_converts_no_price_to_yes_book_ask_price():
    order = ProposedOrder(
        ticker="TEST",
        book_side=BookSide.ASK,
        outcome=OutcomeSide.NO,
        count=2,
        price=0.37,
        client_order_id="cid",
    )
    payload = event_order_payload(order)
    assert payload["side"] == "ask"
    assert payload["price"] == "0.6300"


def test_reduce_only_yes_exit_uses_yes_book_ask_price():
    order = ProposedOrder(
        ticker="TEST",
        book_side=BookSide.ASK,
        outcome=OutcomeSide.YES,
        count=2,
        price=0.12,
        client_order_id="cid",
        reduce_only=True,
    )
    payload = event_order_payload(order)
    assert payload["side"] == "ask"
    assert payload["price"] == "0.1200"
    assert payload["reduce_only"] is True
    assert payload["time_in_force"] == "immediate_or_cancel"
    assert payload["post_only"] is False


def test_reduce_only_no_exit_converts_no_price_to_yes_book_bid_price():
    order = ProposedOrder(
        ticker="TEST",
        book_side=BookSide.BID,
        outcome=OutcomeSide.NO,
        count=2,
        price=0.60,
        client_order_id="cid",
        reduce_only=True,
    )
    payload = event_order_payload(order)
    assert payload["side"] == "bid"
    assert payload["price"] == "0.4000"
    assert payload["reduce_only"] is True
