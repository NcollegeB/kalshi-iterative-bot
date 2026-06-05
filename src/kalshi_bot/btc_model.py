from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Protocol
from socket import timeout as SocketTimeout
from urllib.error import HTTPError, URLError
from urllib.request import Request
from urllib.request import urlopen

from .forecasting import blend_probability_with_market, market_yes_probability, model_market_gap
from .models import Market, TopOfBook


SECONDS_PER_YEAR = 365.25 * 24 * 60 * 60
STRIKE_PATTERN = re.compile(r"-T(?P<strike>\d+(?:\.\d+)?)$")


class BtcMarketClient(Protocol):
    def list_markets(
        self,
        *,
        limit: int = 100,
        cursor: str | None = None,
        status: str = "open",
        series_ticker: str | None = None,
        mve_filter: str | None = None,
    ) -> tuple[list[Market], str | None]: ...

    def get_orderbook(self, ticker: str) -> TopOfBook: ...


@dataclass(frozen=True)
class BtcProbabilityRow:
    ticker: str
    estimated_probability: float
    notes: str
    best_edge: float
    best_side: str
    spot: float
    strike: float
    horizon_minutes: float
    yes_ask: float | None
    no_ask: float | None


def current_coinbase_btc_spot(timeout_seconds: int = 5) -> float:
    errors = []
    for url, price_path in (
        ("https://api.coinbase.com/v2/prices/BTC-USD/spot", ("data", "amount")),
        ("https://api.exchange.coinbase.com/products/BTC-USD/ticker", ("price",)),
    ):
        request = Request(url, headers={"User-Agent": "kalshi-iterative-bot/0.1"})
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            value: object = payload
            for key in price_path:
                value = value[key]  # type: ignore[index]
            return float(value)
        except (HTTPError, URLError, TimeoutError, SocketTimeout, OSError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("Unable to fetch BTC spot: " + "; ".join(errors))


def generate_btc_probability_rows(
    client: BtcMarketClient,
    *,
    spot: float,
    now: datetime,
    series_ticker: str = "KXBTCD",
    limit: int = 100,
    pages: int = 1,
    annual_volatility: float = 0.55,
    probability_shrink: float = 0.75,
    min_edge: float = 0.08,
    max_rows: int = 12,
    max_spread: float | None = None,
    min_horizon_minutes: float | None = None,
    max_horizon_minutes: float | None = None,
    market_blend: float = 0.15,
    max_model_market_gap: float | None = 0.35,
) -> list[BtcProbabilityRow]:
    markets = _fetch_series_markets(client, series_ticker=series_ticker, limit=limit, pages=pages)
    rows: list[BtcProbabilityRow] = []
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
            spot=spot,
            strike=strike,
            horizon_seconds=horizon_seconds,
            annual_volatility=annual_volatility,
        )
        base_probability = shrink_probability(raw_probability, probability_shrink)
        market_probability = market_yes_probability(
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
        )
        gap = model_market_gap(base_probability, market_probability)
        if max_model_market_gap is not None and gap is not None and gap > max_model_market_gap:
            continue
        estimated_probability = blend_probability_with_market(
            base_probability,
            market_probability,
            market_weight=market_blend,
        )
        yes_edge = estimated_probability - yes_ask if yes_ask is not None else float("-inf")
        no_edge = (1.0 - estimated_probability) - no_ask if no_ask is not None else float("-inf")
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
        raw_yes_edge = raw_probability - yes_ask if yes_ask is not None else float("-inf")
        raw_no_edge = (1.0 - raw_probability) - no_ask if no_ask is not None else float("-inf")
        raw_side_edge = raw_yes_edge if best_side == "yes" else raw_no_edge
        if best_edge < min_edge:
            continue
        if raw_side_edge < 0.0:
            continue

        rows.append(
            BtcProbabilityRow(
                ticker=market.ticker,
                estimated_probability=round(estimated_probability, 4),
                notes=(
                    f"BTC lognormal model side={best_side} edge={best_edge:.4f} "
                    f"spot={spot:.2f} strike={strike:.2f} "
                    f"close_time={market.close_time} horizon_min={horizon_minutes:.1f} "
                    f"annual_vol={annual_volatility:.2f} shrink={probability_shrink:.2f} "
                    f"market_blend={market_blend:.2f} market_p_yes={_fmt_optional(market_probability)} "
                    f"model_market_gap={_fmt_optional(gap)} base_p_yes={base_probability:.4f} "
                    f"raw_p_yes={raw_probability:.4f} raw_edge={raw_side_edge:.4f} "
                    f"yes_ask={_fmt_optional(yes_ask)} no_ask={_fmt_optional(no_ask)}"
                ),
                best_edge=round(best_edge, 4),
                best_side=best_side,
                spot=round(spot, 4),
                strike=round(strike, 4),
                horizon_minutes=round(horizon_minutes, 2),
                yes_ask=yes_ask,
                no_ask=no_ask,
            )
        )

    return sorted(rows, key=lambda row: row.best_edge, reverse=True)[:max_rows]


def write_probability_csv(path: Path, rows: list[BtcProbabilityRow]) -> None:
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


def parse_threshold_strike(ticker: str) -> float | None:
    match = STRIKE_PATTERN.search(ticker)
    if not match:
        return None
    return float(match.group("strike"))


def parse_api_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def probability_above_strike(
    *,
    spot: float,
    strike: float,
    horizon_seconds: float,
    annual_volatility: float,
) -> float:
    if spot <= 0 or strike <= 0:
        return 0.0
    if horizon_seconds <= 0 or annual_volatility <= 0:
        return 1.0 if spot >= strike else 0.0

    years = horizon_seconds / SECONDS_PER_YEAR
    sigma_sqrt_t = annual_volatility * math.sqrt(years)
    if sigma_sqrt_t <= 0:
        return 1.0 if spot >= strike else 0.0

    z_score = (math.log(strike / spot) + 0.5 * annual_volatility * annual_volatility * years) / sigma_sqrt_t
    probability = 1.0 - NormalDist().cdf(z_score)
    return max(0.0, min(1.0, probability))


def shrink_probability(probability: float, shrink: float) -> float:
    bounded_shrink = max(0.0, min(1.0, shrink))
    return max(0.0, min(1.0, 0.5 + (probability - 0.5) * bounded_shrink))


def _fetch_series_markets(
    client: BtcMarketClient,
    *,
    series_ticker: str,
    limit: int,
    pages: int,
) -> list[Market]:
    markets: list[Market] = []
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


def _fmt_optional(value: float | None) -> str:
    if value is None:
        return "none"
    return f"{value:.4f}"


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
