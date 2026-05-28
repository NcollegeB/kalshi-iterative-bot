from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from kalshi_bot.kalshi_client import KalshiApiError, KalshiClient
from kalshi_bot.models import Market, OutcomeSide, Signal, TopOfBook


@dataclass(frozen=True)
class ProbabilityEstimate:
    ticker: str
    probability: float
    notes: str


class ProbabilityFileStrategy:
    name = "probability_file"

    def __init__(self, probability_file: Path, min_edge: float) -> None:
        self.probability_file = probability_file
        self.min_edge = min_edge
        self.estimates = {estimate.ticker: estimate for estimate in self._read_estimates()}

    def generate(self, client: KalshiClient, markets: list[Market]) -> list[Signal]:
        signals: list[Signal] = []
        market_by_ticker = {market.ticker: market for market in markets}
        for ticker, estimate in self.estimates.items():
            market = market_by_ticker.get(ticker) or self._fetch_market(client, ticker)
            if not market:
                continue
            top = client.get_orderbook(ticker)
            yes_ask = top.yes_ask
            no_ask = top.no_ask
            metrics = _parse_note_metrics(estimate.notes)
            metadata = _signal_metadata(
                ticker=ticker,
                market=market,
                top=top,
                probability_yes=estimate.probability,
                metrics=metrics,
            )
            if yes_ask is not None:
                yes_edge = estimate.probability - yes_ask
                if yes_edge >= self.min_edge:
                    signals.append(
                        Signal.now(
                            strategy=self.name,
                            ticker=ticker,
                            market_title=market.title,
                            outcome=OutcomeSide.YES,
                            estimated_probability=estimate.probability,
                            reference_price=yes_ask,
                            edge=yes_edge,
                            reason=f"independent probability exceeds YES ask; {estimate.notes}",
                            spread=_outcome_spread(top, OutcomeSide.YES),
                            **metadata,
                        )
                    )
            if no_ask is not None:
                no_probability = 1.0 - estimate.probability
                no_edge = no_probability - no_ask
                if no_edge >= self.min_edge:
                    signals.append(
                        Signal.now(
                            strategy=self.name,
                            ticker=ticker,
                            market_title=market.title,
                            outcome=OutcomeSide.NO,
                            estimated_probability=no_probability,
                            reference_price=no_ask,
                            edge=no_edge,
                            reason=f"independent probability exceeds NO ask; {estimate.notes}",
                            spread=_outcome_spread(top, OutcomeSide.NO),
                            **metadata,
                        )
                    )
        return signals

    def _fetch_market(self, client: KalshiClient, ticker: str) -> Market | None:
        try:
            return client.get_market(ticker)
        except KalshiApiError:
            return None

    def _read_estimates(self) -> list[ProbabilityEstimate]:
        if not self.probability_file.exists():
            return []
        estimates: list[ProbabilityEstimate] = []
        with self.probability_file.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                ticker = (row.get("ticker") or "").strip()
                probability = float(row.get("estimated_probability") or 0)
                notes = (row.get("notes") or "").strip()
                if ticker and 0 < probability < 1:
                    estimates.append(ProbabilityEstimate(ticker=ticker, probability=probability, notes=notes))
        return estimates


def _signal_metadata(
    *,
    ticker: str,
    market: Market,
    top: TopOfBook,
    probability_yes: float,
    metrics: dict[str, str | float],
) -> dict[str, str | float | None]:
    return {
        "asset": _asset_from_metrics_or_ticker(metrics, ticker),
        "model_probability_yes": probability_yes,
        "kalshi_yes_ask": top.yes_ask,
        "kalshi_no_ask": top.no_ask,
        "time_to_close_minutes": _float_metric(metrics, "horizon_min") or _minutes_to_close(market.close_time),
        "annual_volatility": _float_metric(metrics, "annual_vol"),
        "momentum_6h": _float_metric(metrics, "momentum_6h"),
        "raw_probability_yes": _float_metric(metrics, "raw_p_yes"),
        "raw_edge": _float_metric(metrics, "raw_edge"),
    }


def _outcome_spread(top: TopOfBook, outcome: OutcomeSide) -> float | None:
    if outcome == OutcomeSide.YES:
        if top.yes_bid is None or top.yes_ask is None:
            return None
        return round(top.yes_ask - top.yes_bid, 4)
    if top.no_bid is None or top.no_ask is None:
        return None
    return round(top.no_ask - top.no_bid, 4)


def _parse_note_metrics(notes: str) -> dict[str, str | float]:
    metrics: dict[str, str | float] = {}
    parts = notes.split()
    if parts and parts[0].upper() in {"BTC", "ETH", "SOL", "XRP", "DOGE"}:
        metrics["asset"] = parts[0].upper()
    for token in parts:
        if "=" not in token:
            continue
        key, raw_value = token.split("=", 1)
        cleaned = raw_value.strip(",;")
        number = _optional_float(cleaned)
        metrics[key] = number if number is not None else cleaned
    return metrics


def _asset_from_metrics_or_ticker(metrics: dict[str, str | float], ticker: str) -> str | None:
    asset = metrics.get("asset")
    if isinstance(asset, str) and asset:
        return asset.upper()
    if ticker.startswith("KXBTCD"):
        return "BTC"
    if ticker.startswith("KXETHD"):
        return "ETH"
    if ticker.startswith("KXSOLD"):
        return "SOL"
    if ticker.startswith("KXXRPD"):
        return "XRP"
    if ticker.startswith("KXDOGED"):
        return "DOGE"
    return None


def _float_metric(metrics: dict[str, str | float], key: str) -> float | None:
    value = metrics.get(key)
    return value if isinstance(value, float) else None


def _minutes_to_close(close_time: str | None) -> float | None:
    if not close_time:
        return None
    try:
        parsed = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((parsed - datetime.now(timezone.utc)).total_seconds() / 60, 2)


def _optional_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
