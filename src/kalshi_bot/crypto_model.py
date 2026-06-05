from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import stdev
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .btc_model import (
    BtcMarketClient,
    parse_api_datetime,
    parse_threshold_strike,
    probability_above_strike,
    shrink_probability,
)
from .forecasting import blend_probability_with_market, market_yes_probability, model_market_gap
from .model_learning import CalibrationAdjustment


DEFAULT_ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
SECONDS_PER_YEAR = 365.25 * 24 * 60 * 60
MIN_TRADE_PRICE = 0.02
MAX_TRADE_PRICE = 0.98
MIN_RAW_EDGE = 0.0
COINBASE_FETCH_ERRORS = (HTTPError, URLError, TimeoutError, OSError, KeyError, TypeError, ValueError)


@dataclass(frozen=True)
class CryptoAsset:
    symbol: str
    product_id: str
    threshold_series: str
    default_annual_volatility: float


@dataclass(frozen=True)
class CryptoMarketState:
    spot: float
    annual_volatility: float
    volatility_source: str
    momentum_6h: float | None


@dataclass(frozen=True)
class CryptoProbabilityRow:
    ticker: str
    estimated_probability: float
    notes: str
    best_edge: float
    best_side: str
    symbol: str
    spot: float
    strike: float
    horizon_minutes: float
    yes_ask: float | None
    no_ask: float | None


ASSETS: dict[str, CryptoAsset] = {
    "BTC": CryptoAsset("BTC", "BTC-USD", "KXBTCD", 0.55),
    "ETH": CryptoAsset("ETH", "ETH-USD", "KXETHD", 0.70),
    "SOL": CryptoAsset("SOL", "SOL-USD", "KXSOLD", 0.95),
    "XRP": CryptoAsset("XRP", "XRP-USD", "KXXRPD", 0.90),
    "DOGE": CryptoAsset("DOGE", "DOGE-USD", "KXDOGED", 1.10),
}


def generate_crypto_probability_rows(
    client: BtcMarketClient,
    *,
    assets: list[str] | tuple[str, ...] = DEFAULT_ASSETS,
    now: datetime,
    limit: int = 100,
    pages: int = 1,
    probability_shrink: float = 0.70,
    min_edge: float = 0.08,
    max_rows_per_asset: int = 4,
    max_spread: float | None = None,
    min_horizon_minutes: float | None = None,
    max_horizon_minutes: float | None = None,
    calibration_adjustments: Mapping[str, CalibrationAdjustment] | None = None,
    market_blend: float = 0.15,
    max_model_market_gap: float | None = 0.35,
    min_annual_volatility: float | None = 0.0,
    max_annual_volatility: float | None = 1.75,
) -> list[CryptoProbabilityRow]:
    rows: list[CryptoProbabilityRow] = []
    for symbol in assets:
        asset = ASSETS[symbol.upper()]
        try:
            state = fetch_crypto_market_state(asset)
        except COINBASE_FETCH_ERRORS:
            continue
        asset_rows = _generate_asset_rows(
            client,
            asset=asset,
            state=state,
            now=now,
            limit=limit,
            pages=pages,
            probability_shrink=probability_shrink,
            min_edge=min_edge,
            max_rows=max_rows_per_asset,
            max_spread=max_spread,
            min_horizon_minutes=min_horizon_minutes,
            max_horizon_minutes=max_horizon_minutes,
            calibration_adjustment=(calibration_adjustments or {}).get(asset.symbol),
            market_blend=market_blend,
            max_model_market_gap=max_model_market_gap,
            min_annual_volatility=min_annual_volatility,
            max_annual_volatility=max_annual_volatility,
        )
        rows.extend(asset_rows)
    return sorted(rows, key=lambda row: row.best_edge, reverse=True)


def fetch_crypto_market_state(asset: CryptoAsset) -> CryptoMarketState:
    spot = fetch_coinbase_spot(asset.product_id)
    candles = fetch_coinbase_candles(asset.product_id)
    realized_vol = annualized_realized_volatility(candles)
    momentum_6h = log_momentum(candles, periods=6)
    if realized_vol is None:
        return CryptoMarketState(
            spot=spot,
            annual_volatility=asset.default_annual_volatility,
            volatility_source=f"default_{asset.default_annual_volatility:.2f}",
            momentum_6h=momentum_6h,
        )
    return CryptoMarketState(
        spot=spot,
        annual_volatility=realized_vol,
        volatility_source="coinbase_1h_realized",
        momentum_6h=momentum_6h,
    )


def fetch_coinbase_spot(product_id: str, timeout_seconds: int = 5) -> float:
    base_url = f"https://api.coinbase.com/v2/prices/{product_id}/spot"
    request = Request(base_url, headers={"User-Agent": "kalshi-iterative-bot/0.1"})
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return float(payload["data"]["amount"])


def fetch_coinbase_candles(
    product_id: str,
    *,
    granularity: int = 3600,
    timeout_seconds: int = 5,
) -> list[dict[str, float]]:
    query = urlencode({"granularity": granularity})
    request = Request(
        f"https://api.exchange.coinbase.com/products/{product_id}/candles?{query}",
        headers={"User-Agent": "kalshi-iterative-bot/0.1"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except COINBASE_FETCH_ERRORS:
        return []
    candles = [
        {
            "time": float(raw[0]),
            "low": float(raw[1]),
            "high": float(raw[2]),
            "open": float(raw[3]),
            "close": float(raw[4]),
            "volume": float(raw[5]),
        }
        for raw in payload
        if len(raw) >= 6
    ]
    return sorted(candles, key=lambda candle: candle["time"])


def annualized_realized_volatility(candles: list[dict[str, float]]) -> float | None:
    closes = [candle["close"] for candle in candles if candle["close"] > 0]
    if len(closes) < 12:
        return None
    returns = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes))]
    if len(returns) < 2:
        return None
    return round(stdev(returns) * math.sqrt(SECONDS_PER_YEAR / 3600), 4)


def log_momentum(candles: list[dict[str, float]], *, periods: int) -> float | None:
    closes = [candle["close"] for candle in candles if candle["close"] > 0]
    if len(closes) <= periods:
        return None
    return round(math.log(closes[-1] / closes[-1 - periods]), 6)


def write_crypto_probability_csv(path: Path, rows: list[CryptoProbabilityRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ticker", "estimated_probability", "notes"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "ticker": row.ticker,
                    "estimated_probability": f"{row.estimated_probability:.4f}",
                    "notes": row.notes,
                }
            )


def _generate_asset_rows(
    client: BtcMarketClient,
    *,
    asset: CryptoAsset,
    state: CryptoMarketState,
    now: datetime,
    limit: int,
    pages: int,
    probability_shrink: float,
    min_edge: float,
    max_rows: int,
    max_spread: float | None = None,
    min_horizon_minutes: float | None = None,
    max_horizon_minutes: float | None = None,
    calibration_adjustment: CalibrationAdjustment | None = None,
    market_blend: float = 0.15,
    max_model_market_gap: float | None = 0.35,
    min_annual_volatility: float | None = 0.0,
    max_annual_volatility: float | None = 1.75,
) -> list[CryptoProbabilityRow]:
    if not _volatility_in_regime(
        state.annual_volatility,
        min_annual_volatility=min_annual_volatility,
        max_annual_volatility=max_annual_volatility,
    ):
        return []

    rows: list[CryptoProbabilityRow] = []
    markets = _fetch_series_markets(client, series_ticker=asset.threshold_series, limit=limit, pages=pages)
    for market in markets:
        strike = parse_threshold_strike(market.ticker)
        close_time = parse_api_datetime(market.close_time)
        if strike is None or close_time is None:
            continue
        horizon_seconds = (close_time - now).total_seconds()
        if horizon_seconds <= 0:
            continue
        horizon_minutes = horizon_seconds / 60
        if min_horizon_minutes is not None and horizon_minutes < min_horizon_minutes:
            continue
        if max_horizon_minutes is not None and horizon_minutes > max_horizon_minutes:
            continue

        yes_ask = _complement_price(market.no_bid)
        no_ask = _complement_price(market.yes_bid)
        yes_bid = market.yes_bid
        no_bid = market.no_bid
        if yes_ask is None and no_ask is None:
            top = client.get_orderbook(market.ticker)
            yes_ask = top.yes_ask
            no_ask = top.no_ask
            yes_bid = top.yes_bid
            no_bid = top.no_bid
        if yes_ask is None and no_ask is None:
            continue

        raw_probability = probability_above_strike(
            spot=state.spot,
            strike=strike,
            horizon_seconds=horizon_seconds,
            annual_volatility=state.annual_volatility,
        )
        base_probability = shrink_probability(raw_probability, probability_shrink)
        calibrated_probability = _apply_calibration(base_probability, calibration_adjustment)
        market_probability = market_yes_probability(
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
        )
        gap = model_market_gap(calibrated_probability, market_probability)
        if max_model_market_gap is not None and gap is not None and gap > max_model_market_gap:
            continue
        estimated_probability = blend_probability_with_market(
            calibrated_probability,
            market_probability,
            market_weight=market_blend,
        )
        yes_edge = (
            estimated_probability - yes_ask if yes_ask is not None and _tradable_price(yes_ask) else float("-inf")
        )
        no_edge = (
            (1.0 - estimated_probability) - no_ask
            if no_ask is not None and _tradable_price(no_ask)
            else float("-inf")
        )
        best_side = "yes" if yes_edge >= no_edge else "no"
        best_edge = max(yes_edge, no_edge)
        best_spread = _outcome_spread(
            yes_bid=yes_bid,
            no_bid=no_bid,
            yes_ask=yes_ask,
            no_ask=no_ask,
            side=best_side,
        )
        if max_spread is not None and (best_spread is None or best_spread > max_spread):
            continue
        raw_yes_edge = raw_probability - yes_ask if yes_ask is not None and _tradable_price(yes_ask) else float("-inf")
        raw_no_edge = (
            (1.0 - raw_probability) - no_ask
            if no_ask is not None and _tradable_price(no_ask)
            else float("-inf")
        )
        raw_side_edge = raw_yes_edge if best_side == "yes" else raw_no_edge
        if best_edge < min_edge:
            continue
        if raw_side_edge < MIN_RAW_EDGE:
            continue

        rows.append(
            CryptoProbabilityRow(
                ticker=market.ticker,
                estimated_probability=round(estimated_probability, 4),
                notes=(
                    f"{asset.symbol} crypto model side={best_side} edge={best_edge:.4f} "
                    f"spot={state.spot:.6g} strike={strike:.6g} close_time={market.close_time} "
                    f"horizon_min={horizon_minutes:.1f} annual_vol={state.annual_volatility:.4f} "
                    f"vol_source={state.volatility_source} shrink={probability_shrink:.2f} "
                    f"market_blend={market_blend:.2f} market_p_yes={_fmt_optional(market_probability)} "
                    f"model_market_gap={_fmt_optional(gap)} "
                    f"vol_regime={_fmt_optional(min_annual_volatility)}-{_fmt_optional(max_annual_volatility)} "
                    f"momentum_6h={_fmt_optional(state.momentum_6h)} base_p_yes={base_probability:.4f} "
                    f"calibrated_p_yes={calibrated_probability:.4f} "
                    f"raw_p_yes={raw_probability:.4f} "
                    f"raw_edge={raw_side_edge:.4f} "
                    f"{_calibration_note(calibration_adjustment)}"
                    f"yes_ask={_fmt_optional(yes_ask)} no_ask={_fmt_optional(no_ask)}"
                ),
                best_edge=round(best_edge, 4),
                best_side=best_side,
                symbol=asset.symbol,
                spot=round(state.spot, 8),
                strike=round(strike, 8),
                horizon_minutes=round(horizon_minutes, 2),
                yes_ask=yes_ask,
                no_ask=no_ask,
            )
        )
    return sorted(rows, key=lambda row: row.best_edge, reverse=True)[:max_rows]


def _fetch_series_markets(
    client: BtcMarketClient,
    *,
    series_ticker: str,
    limit: int,
    pages: int,
):
    markets = []
    cursor = None
    for _ in range(pages):
        page, cursor = client.list_markets(
            limit=limit,
            cursor=cursor,
            status="open",
            series_ticker=series_ticker,
            mve_filter="exclude",
        )
        markets.extend(page)
        if not cursor:
            break
    return markets


def _complement_price(value: float | None) -> float | None:
    if value is None:
        return None
    return round(1.0 - value, 4)


def _outcome_spread(
    *,
    yes_bid: float | None,
    no_bid: float | None,
    yes_ask: float | None,
    no_ask: float | None,
    side: str,
) -> float | None:
    if side == "yes":
        if yes_bid is None or yes_ask is None:
            return None
        return round(yes_ask - yes_bid, 4)
    if no_bid is None or no_ask is None:
        return None
    return round(no_ask - no_bid, 4)


def _tradable_price(value: float) -> bool:
    return MIN_TRADE_PRICE <= value <= MAX_TRADE_PRICE


def _apply_calibration(probability: float, adjustment: CalibrationAdjustment | None) -> float:
    if adjustment is None:
        return probability
    return max(0.001, min(0.999, probability + adjustment.adjustment))


def _volatility_in_regime(
    annual_volatility: float,
    *,
    min_annual_volatility: float | None,
    max_annual_volatility: float | None,
) -> bool:
    if min_annual_volatility is not None and annual_volatility < min_annual_volatility:
        return False
    if max_annual_volatility is not None and annual_volatility > max_annual_volatility:
        return False
    return True


def _calibration_note(adjustment: CalibrationAdjustment | None) -> str:
    if adjustment is None:
        return ""
    return (
        f"cal_adj={adjustment.adjustment:.4f} cal_n={adjustment.samples} "
        f"cal_actual_yes={adjustment.actual_yes_rate:.4f} cal_avg_p_yes={adjustment.avg_probability_yes:.4f} "
    )


def _fmt_optional(value: float | None) -> str:
    if value is None:
        return "none"
    return f"{value:.4f}"
