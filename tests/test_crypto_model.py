from datetime import datetime, timezone

from kalshi_bot.crypto_model import (
    CryptoAsset,
    CryptoMarketState,
    annualized_realized_volatility,
    fetch_coinbase_candles,
    generate_crypto_probability_rows,
    log_momentum,
    _generate_asset_rows,
    _tradable_price,
)
from kalshi_bot.model_learning import CalibrationAdjustment
from kalshi_bot.models import Market, TopOfBook
from kalshi_bot.orderflow import OrderflowAnalyzer, OrderflowConfig


class FakeCryptoClient:
    def __init__(
        self,
        *,
        ticker_suffix: str = "T100",
        yes_bid: float = 0.30,
        no_bid: float = 0.60,
        yes_bid_size: float = 10,
        no_bid_size: float = 10,
        trades: list[dict] | None = None,
    ):
        self.ticker_suffix = ticker_suffix
        self.yes_bid = yes_bid
        self.no_bid = no_bid
        self.yes_bid_size = yes_bid_size
        self.no_bid_size = no_bid_size
        self.trades = trades or []

    def list_markets(self, **kwargs):
        series = kwargs["series_ticker"]
        return (
            [
                Market(
                    ticker=f"{series}-26MAY2817-{self.ticker_suffix}",
                    title="Crypto price on May 28?",
                    event_ticker=f"{series}-26MAY2817",
                    status="active",
                    volume=100,
                    yes_bid=self.yes_bid,
                    no_bid=self.no_bid,
                    close_time="2026-05-28T21:00:00Z",
                    settlement_value=None,
                    settlement_ts=None,
                    category="Crypto",
                    yes_bid_size=self.yes_bid_size,
                    no_bid_size=self.no_bid_size,
                )
            ],
            None,
        )

    def get_orderbook(self, ticker):
        return TopOfBook(
            yes_bid=self.yes_bid,
            yes_bid_size=self.yes_bid_size,
            no_bid=self.no_bid,
            no_bid_size=self.no_bid_size,
        )

    def list_trades(self, **kwargs):
        return self.trades, None


def test_realized_volatility_uses_candle_returns():
    candles = [{"close": 100 + index} for index in range(20)]
    volatility = annualized_realized_volatility(candles)
    assert volatility is not None
    assert volatility > 0


def test_log_momentum_uses_recent_close_change():
    candles = [{"close": 100.0 + index} for index in range(10)]
    assert log_momentum(candles, periods=6) > 0


def test_coinbase_candles_treats_connection_reset_as_missing_data(monkeypatch):
    def raise_connection_reset(*args, **kwargs):
        raise ConnectionResetError("reset")

    monkeypatch.setattr("kalshi_bot.crypto_model.urlopen", raise_connection_reset)

    assert fetch_coinbase_candles("ETH-USD") == []


def test_generate_crypto_rows_skips_asset_when_coinbase_state_resets(monkeypatch):
    def raise_connection_reset(asset):
        raise ConnectionResetError("reset")

    monkeypatch.setattr("kalshi_bot.crypto_model.fetch_crypto_market_state", raise_connection_reset)

    rows = generate_crypto_probability_rows(
        FakeCryptoClient(),
        assets=["ETH"],
        now=datetime(2026, 5, 27, 21, tzinfo=timezone.utc),
    )

    assert rows == []


def test_generate_asset_rows_includes_asset_and_fact_inputs():
    rows = _generate_asset_rows(
        FakeCryptoClient(),
        asset=CryptoAsset("ETH", "ETH-USD", "KXETHD", 0.70),
        state=CryptoMarketState(
            spot=100,
            annual_volatility=0.70,
            volatility_source="coinbase_1h_realized",
            momentum_6h=0.01,
        ),
        now=datetime(2026, 5, 27, 21, tzinfo=timezone.utc),
        limit=100,
        pages=1,
        probability_shrink=1.0,
        min_edge=-1.0,
        max_rows=4,
        base_slippage=0.0,
        spread_slippage_factor=0.0,
    )

    assert len(rows) == 1
    assert rows[0].symbol == "ETH"
    assert "vol_source=coinbase_1h_realized" in rows[0].notes
    assert "momentum_6h=0.0100" in rows[0].notes
    assert "raw_edge=" in rows[0].notes


def test_generate_asset_rows_uses_orderflow_adjusted_net_edge():
    client = FakeCryptoClient(
        yes_bid=0.30,
        no_bid=0.60,
        yes_bid_size=80,
        no_bid_size=20,
        trades=[
            {
                "ticker": "KXETHD-26MAY2817-T100",
                "count_fp": "40.00",
                "created_time": "2026-05-27T20:00:00Z",
                "taker_outcome_side": "yes",
                "yes_price_dollars": "0.4100",
            },
            {
                "ticker": "KXETHD-26MAY2817-T100",
                "count_fp": "30.00",
                "created_time": "2026-05-27T20:01:00Z",
                "taker_outcome_side": "yes",
                "yes_price_dollars": "0.4300",
            },
            {
                "ticker": "KXETHD-26MAY2817-T100",
                "count_fp": "10.00",
                "created_time": "2026-05-27T20:02:00Z",
                "taker_outcome_side": "no",
                "yes_price_dollars": "0.4200",
            },
        ],
    )
    rows = _generate_asset_rows(
        client,
        asset=CryptoAsset("ETH", "ETH-USD", "KXETHD", 0.70),
        state=CryptoMarketState(
            spot=100,
            annual_volatility=0.70,
            volatility_source="coinbase_1h_realized",
            momentum_6h=0.01,
        ),
        now=datetime(2026, 5, 27, 21, tzinfo=timezone.utc),
        limit=100,
        pages=1,
        probability_shrink=1.0,
        market_blend=0.0,
        max_model_market_gap=None,
        min_edge=0.05,
        max_rows=4,
        base_slippage=0.0,
        spread_slippage_factor=0.0,
        orderflow_analyzer=OrderflowAnalyzer(
            client,
            OrderflowConfig(min_trades=1, min_contracts=1, large_trade_contracts=25),
        ),
    )

    assert len(rows) == 1
    assert "crypto_orderflow_model" in rows[0].notes
    assert "orderflow_adj=" in rows[0].notes
    assert "orderflow_reason=recent_taker_and_book_pressure" in rows[0].notes
    assert "slippage_penalty=" in rows[0].notes
    assert "fee_haircut=" in rows[0].notes


def test_generate_asset_rows_rejects_shrink_only_tail_edge():
    rows = _generate_asset_rows(
        FakeCryptoClient(ticker_suffix="T200", yes_bid=0.0, no_bid=0.98),
        asset=CryptoAsset("ETH", "ETH-USD", "KXETHD", 0.70),
        state=CryptoMarketState(
            spot=100,
            annual_volatility=0.20,
            volatility_source="coinbase_1h_realized",
            momentum_6h=0.0,
        ),
        now=datetime(2026, 5, 27, 21, tzinfo=timezone.utc),
        limit=100,
        pages=1,
        probability_shrink=0.70,
        min_edge=0.05,
        max_rows=4,
    )

    assert rows == []


def test_generate_asset_rows_applies_spread_and_horizon_filters():
    now = datetime(2026, 5, 27, 21, tzinfo=timezone.utc)
    common = {
        "asset": CryptoAsset("ETH", "ETH-USD", "KXETHD", 0.70),
        "state": CryptoMarketState(
            spot=100,
            annual_volatility=0.70,
            volatility_source="coinbase_1h_realized",
            momentum_6h=0.01,
        ),
        "now": now,
        "limit": 100,
        "pages": 1,
        "probability_shrink": 1.0,
        "market_blend": 0.0,
        "max_model_market_gap": None,
        "min_edge": -1.0,
        "max_rows": 4,
        "base_slippage": 0.0,
        "spread_slippage_factor": 0.0,
    }

    wide_spread_rows = _generate_asset_rows(FakeCryptoClient(), max_spread=0.02, **common)
    long_horizon_rows = _generate_asset_rows(FakeCryptoClient(), max_horizon_minutes=60, **common)

    assert wide_spread_rows == []
    assert long_horizon_rows == []


def test_generate_asset_rows_applies_calibration_adjustment():
    now = datetime(2026, 5, 27, 21, tzinfo=timezone.utc)
    common = {
        "asset": CryptoAsset("ETH", "ETH-USD", "KXETHD", 0.70),
        "state": CryptoMarketState(
            spot=100,
            annual_volatility=0.70,
            volatility_source="coinbase_1h_realized",
            momentum_6h=0.01,
        ),
        "now": now,
        "limit": 100,
        "pages": 1,
        "probability_shrink": 1.0,
        "market_blend": 0.0,
        "max_model_market_gap": None,
        "min_edge": -1.0,
        "max_rows": 4,
        "base_slippage": 0.0,
        "spread_slippage_factor": 0.0,
    }
    base_rows = _generate_asset_rows(FakeCryptoClient(), **common)
    calibrated_rows = _generate_asset_rows(
        FakeCryptoClient(),
        calibration_adjustment=CalibrationAdjustment(
            asset="ETH",
            samples=20,
            avg_probability_yes=0.4,
            actual_yes_rate=0.6,
            bias=0.2,
            adjustment=0.1,
            brier_score=0.2,
            log_loss=0.5,
        ),
        **common,
    )

    assert calibrated_rows[0].estimated_probability == round(base_rows[0].estimated_probability + 0.1, 4)
    assert "cal_adj=0.1000" in calibrated_rows[0].notes


def test_generate_asset_rows_blends_probability_toward_market_midpoint():
    common = {
        "asset": CryptoAsset("ETH", "ETH-USD", "KXETHD", 0.70),
        "state": CryptoMarketState(
            spot=100,
            annual_volatility=0.70,
            volatility_source="coinbase_1h_realized",
            momentum_6h=0.01,
        ),
        "now": datetime(2026, 5, 27, 21, tzinfo=timezone.utc),
        "limit": 100,
        "pages": 1,
        "probability_shrink": 1.0,
        "max_model_market_gap": None,
        "min_edge": -1.0,
        "max_rows": 4,
        "base_slippage": 0.0,
        "spread_slippage_factor": 0.0,
    }
    base_rows = _generate_asset_rows(
        FakeCryptoClient(yes_bid=0.30, no_bid=0.60),
        market_blend=0.0,
        **common,
    )
    blended_rows = _generate_asset_rows(
        FakeCryptoClient(yes_bid=0.30, no_bid=0.60),
        market_blend=0.5,
        **common,
    )

    assert blended_rows[0].estimated_probability < base_rows[0].estimated_probability
    assert "market_blend=0.50" in blended_rows[0].notes
    assert "market_p_yes=0.3500" in blended_rows[0].notes


def test_generate_asset_rows_rejects_large_model_market_gap():
    rows = _generate_asset_rows(
        FakeCryptoClient(ticker_suffix="T100", yes_bid=0.01, no_bid=0.98),
        asset=CryptoAsset("ETH", "ETH-USD", "KXETHD", 0.70),
        state=CryptoMarketState(
            spot=100,
            annual_volatility=0.70,
            volatility_source="coinbase_1h_realized",
            momentum_6h=0.01,
        ),
        now=datetime(2026, 5, 27, 21, tzinfo=timezone.utc),
        limit=100,
        pages=1,
        probability_shrink=1.0,
        market_blend=0.0,
        max_model_market_gap=0.10,
        min_edge=0.05,
        max_rows=4,
    )

    assert rows == []


def test_generate_asset_rows_rejects_out_of_regime_volatility():
    rows = _generate_asset_rows(
        FakeCryptoClient(),
        asset=CryptoAsset("ETH", "ETH-USD", "KXETHD", 0.70),
        state=CryptoMarketState(
            spot=100,
            annual_volatility=2.25,
            volatility_source="coinbase_1h_realized",
            momentum_6h=0.01,
        ),
        now=datetime(2026, 5, 27, 21, tzinfo=timezone.utc),
        limit=100,
        pages=1,
        probability_shrink=1.0,
        min_edge=0.05,
        max_rows=4,
        max_annual_volatility=1.75,
    )

    assert rows == []


def test_tradable_price_filter_skips_extreme_quotes():
    assert not _tradable_price(0.0)
    assert not _tradable_price(0.01)
    assert _tradable_price(0.02)
    assert _tradable_price(0.98)
    assert not _tradable_price(0.99)
