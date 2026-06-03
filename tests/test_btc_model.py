from datetime import datetime, timezone
from pathlib import Path

from kalshi_bot.btc_model import (
    current_coinbase_btc_spot,
    generate_btc_probability_rows,
    parse_threshold_strike,
    probability_above_strike,
    shrink_probability,
    write_probability_csv,
)
from kalshi_bot.models import Market, TopOfBook


class FakeBtcClient:
    def __init__(self, *, ticker_suffix: str = "T100", yes_bid: float = 0.30, no_bid: float = 0.60):
        self.ticker_suffix = ticker_suffix
        self.yes_bid = yes_bid
        self.no_bid = no_bid

    def list_markets(self, **kwargs):
        return (
            [
                Market(
                    ticker=f"KXBTCD-26MAY2817-{self.ticker_suffix}",
                    title="Bitcoin price on May 28?",
                    event_ticker="KXBTCD-26MAY2817",
                    status="active",
                    volume=100,
                    yes_bid=self.yes_bid,
                    no_bid=self.no_bid,
                    close_time="2026-05-28T21:00:00Z",
                    settlement_value=None,
                    settlement_ts=None,
                    category="Crypto",
                )
            ],
            None,
        )

    def get_orderbook(self, ticker):
        return TopOfBook(yes_bid=self.yes_bid, yes_bid_size=10, no_bid=self.no_bid, no_bid_size=10)


def test_parse_threshold_strike():
    assert parse_threshold_strike("KXBTCD-26MAY2817-T85249.99") == 85249.99
    assert parse_threshold_strike("KXBTC15M-26MAY271715-15") is None


def test_probability_above_strike_is_reasonable_near_spot():
    probability = probability_above_strike(
        spot=100,
        strike=100,
        horizon_seconds=24 * 60 * 60,
        annual_volatility=0.55,
    )
    assert 0.48 < probability < 0.52


def test_shrink_probability_pulls_toward_half():
    assert shrink_probability(0.90, 0.50) == 0.70


def test_generate_btc_rows_includes_horizon_and_edge_notes():
    rows = generate_btc_probability_rows(
        FakeBtcClient(),
        spot=100,
        now=datetime(2026, 5, 27, 21, tzinfo=timezone.utc),
        annual_volatility=0.55,
        probability_shrink=1.0,
        min_edge=0.05,
    )

    assert len(rows) == 1
    assert rows[0].ticker == "KXBTCD-26MAY2817-T100"
    assert rows[0].best_side == "yes"
    assert "horizon_min=1440.0" in rows[0].notes
    assert "raw_edge=" in rows[0].notes


def test_generate_btc_rows_blends_probability_toward_market_midpoint():
    common = {
        "spot": 100,
        "now": datetime(2026, 5, 27, 21, tzinfo=timezone.utc),
        "annual_volatility": 0.55,
        "probability_shrink": 1.0,
        "max_model_market_gap": None,
        "min_edge": 0.01,
    }
    base_rows = generate_btc_probability_rows(
        FakeBtcClient(yes_bid=0.30, no_bid=0.60),
        market_blend=0.0,
        **common,
    )
    blended_rows = generate_btc_probability_rows(
        FakeBtcClient(yes_bid=0.30, no_bid=0.60),
        market_blend=0.5,
        **common,
    )

    assert blended_rows[0].estimated_probability < base_rows[0].estimated_probability
    assert "market_blend=0.50" in blended_rows[0].notes
    assert "market_p_yes=0.3500" in blended_rows[0].notes


def test_generate_btc_rows_rejects_large_model_market_gap():
    rows = generate_btc_probability_rows(
        FakeBtcClient(ticker_suffix="T100", yes_bid=0.01, no_bid=0.98),
        spot=100,
        now=datetime(2026, 5, 27, 21, tzinfo=timezone.utc),
        annual_volatility=0.55,
        probability_shrink=1.0,
        market_blend=0.0,
        max_model_market_gap=0.10,
        min_edge=0.05,
    )

    assert rows == []


def test_generate_btc_rows_rejects_shrink_only_tail_edge():
    rows = generate_btc_probability_rows(
        FakeBtcClient(ticker_suffix="T200", yes_bid=0.0, no_bid=0.98),
        spot=100,
        now=datetime(2026, 5, 27, 21, tzinfo=timezone.utc),
        annual_volatility=0.20,
        probability_shrink=0.70,
        min_edge=0.05,
    )

    assert rows == []


def test_generate_btc_rows_applies_spread_and_horizon_filters():
    now = datetime(2026, 5, 27, 21, tzinfo=timezone.utc)

    wide_spread_rows = generate_btc_probability_rows(
        FakeBtcClient(),
        spot=100,
        now=now,
        annual_volatility=0.55,
        probability_shrink=1.0,
        min_edge=0.05,
        max_spread=0.02,
    )
    long_horizon_rows = generate_btc_probability_rows(
        FakeBtcClient(),
        spot=100,
        now=now,
        annual_volatility=0.55,
        probability_shrink=1.0,
        min_edge=0.05,
        max_horizon_minutes=60,
    )

    assert wide_spread_rows == []
    assert long_horizon_rows == []


def test_write_probability_csv(tmp_path: Path):
    rows = generate_btc_probability_rows(
        FakeBtcClient(),
        spot=100,
        now=datetime(2026, 5, 27, 21, tzinfo=timezone.utc),
        annual_volatility=0.55,
        probability_shrink=1.0,
        min_edge=0.05,
    )
    output = tmp_path / "probabilities.csv"

    write_probability_csv(output, rows)

    assert output.read_text().startswith("ticker,estimated_probability,notes\n")


def test_current_coinbase_btc_spot_treats_timeout_as_fetch_failure(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr("kalshi_bot.btc_model.urlopen", raise_timeout)

    try:
        current_coinbase_btc_spot()
    except RuntimeError as exc:
        assert "Unable to fetch BTC spot" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
