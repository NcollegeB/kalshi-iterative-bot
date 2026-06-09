from pathlib import Path

from kalshi_bot.models import Market, TopOfBook
from kalshi_bot.strategies.probability_file import ProbabilityFileStrategy


class FakeProbabilityClient:
    def __init__(self, top: TopOfBook):
        self.top = top

    def get_orderbook(self, ticker):
        return self.top

    def get_market(self, ticker):
        return Market(
            ticker=ticker,
            title="Test market",
            event_ticker="TEST",
            status="active",
            volume=100,
            yes_bid=self.top.yes_bid,
            no_bid=self.top.no_bid,
            close_time="2026-06-08T21:00:00Z",
            settlement_value=None,
            settlement_ts=None,
            category="Crypto",
        )


def write_probability_file(path: Path, probability: float) -> None:
    path.write_text(
        "ticker,estimated_probability,notes\n"
        f"TEST, {probability}, BTC crypto_orderflow_model side=yes edge=0.1000 horizon_min=30\n"
    )


def test_probability_file_rejects_raw_edge_that_fails_after_costs(tmp_path):
    probability_file = tmp_path / "probabilities.csv"
    write_probability_file(probability_file, 0.69)
    strategy = ProbabilityFileStrategy(
        probability_file,
        min_edge=0.08,
        base_slippage=0.01,
        spread_slippage_factor=0.0,
    )

    signals = strategy.generate(FakeProbabilityClient(TopOfBook(yes_bid=0.50, yes_bid_size=10, no_bid=0.40, no_bid_size=10)), [])

    assert signals == []


def test_probability_file_records_edge_before_fee_when_net_edge_passes(tmp_path):
    probability_file = tmp_path / "probabilities.csv"
    write_probability_file(probability_file, 0.75)
    strategy = ProbabilityFileStrategy(
        probability_file,
        min_edge=0.08,
        base_slippage=0.01,
        spread_slippage_factor=0.0,
    )

    signals = strategy.generate(FakeProbabilityClient(TopOfBook(yes_bid=0.50, yes_bid_size=10, no_bid=0.40, no_bid_size=10)), [])

    assert len(signals) == 1
    assert signals[0].edge == 0.14
    assert "net_edge_after_costs=0.1200" in signals[0].reason
