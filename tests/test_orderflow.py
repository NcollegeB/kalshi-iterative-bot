from datetime import datetime, timezone

from kalshi_bot.models import TopOfBook
from kalshi_bot.orderflow import OrderflowAnalyzer, OrderflowConfig, summarize_orderflow


class FakeTradeClient:
    def __init__(self, trades):
        self.trades = trades
        self.calls = 0

    def list_trades(self, **kwargs):
        self.calls += 1
        return self.trades, None


def test_orderflow_positive_yes_pressure_adds_probability():
    trades = [
        {
            "count_fp": "40.00",
            "created_time": "2026-06-08T20:00:00Z",
            "taker_outcome_side": "yes",
            "yes_price_dollars": "0.5200",
        },
        {
            "count_fp": "30.00",
            "created_time": "2026-06-08T20:01:00Z",
            "taker_outcome_side": "yes",
            "yes_price_dollars": "0.5600",
        },
        {
            "count_fp": "10.00",
            "created_time": "2026-06-08T20:02:00Z",
            "taker_outcome_side": "no",
            "yes_price_dollars": "0.5500",
        },
    ]

    summary = summarize_orderflow(
        trades,
        top=TopOfBook(yes_bid=0.52, yes_bid_size=80, no_bid=0.42, no_bid_size=20),
        config=OrderflowConfig(min_trades=1, min_contracts=1, large_trade_contracts=25),
    )

    assert summary.trade_count == 3
    assert summary.trade_imbalance > 0
    assert summary.large_trade_imbalance > 0
    assert summary.book_imbalance > 0
    assert summary.probability_adjustment > 0


def test_orderflow_requires_enough_recent_flow_before_adjusting():
    summary = summarize_orderflow(
        [
            {
                "count_fp": "2.00",
                "created_time": "2026-06-08T20:00:00Z",
                "taker_outcome_side": "yes",
                "yes_price_dollars": "0.5200",
            }
        ],
        top=TopOfBook(yes_bid=0.52, yes_bid_size=80, no_bid=0.42, no_bid_size=20),
        config=OrderflowConfig(min_trades=3, min_contracts=20),
    )

    assert summary.reason == "insufficient_recent_flow"
    assert summary.probability_adjustment == 0


def test_orderflow_prefetch_reuses_bulk_trade_response_for_later_tickers():
    client = FakeTradeClient(
        [
            {
                "ticker": "BTC",
                "count_fp": "40.00",
                "created_time": "2026-06-08T20:00:00Z",
                "taker_outcome_side": "yes",
                "yes_price_dollars": "0.5200",
            },
            {
                "ticker": "ETH",
                "count_fp": "40.00",
                "created_time": "2026-06-08T20:00:00Z",
                "taker_outcome_side": "no",
                "yes_price_dollars": "0.4800",
            },
        ]
    )
    analyzer = OrderflowAnalyzer(client, OrderflowConfig(min_trades=1, min_contracts=1))
    top = TopOfBook(yes_bid=0.50, yes_bid_size=10, no_bid=0.49, no_bid_size=10)
    now = datetime(2026, 6, 8, 20, 5, tzinfo=timezone.utc)

    analyzer.prefetch(["BTC"], now=now)
    btc = analyzer.summarize("BTC", top=top, now=now)
    analyzer.prefetch(["ETH"], now=now)
    eth = analyzer.summarize("ETH", top=top, now=now)

    assert client.calls == 1
    assert btc.yes_taker_contracts == 40
    assert eth.no_taker_contracts == 40
