from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .kalshi_client import KalshiApiError
from .models import TopOfBook


@dataclass(frozen=True)
class OrderflowConfig:
    enabled: bool = True
    lookback_minutes: float = 30.0
    min_trades: int = 3
    min_contracts: float = 20.0
    large_trade_contracts: float = 25.0
    max_probability_adjustment: float = 0.04
    trade_weight: float = 0.50
    large_trade_weight: float = 0.20
    book_weight: float = 0.20
    momentum_weight: float = 0.10


@dataclass(frozen=True)
class OrderflowSummary:
    enabled: bool
    trade_count: int = 0
    contract_volume: float = 0.0
    yes_taker_contracts: float = 0.0
    no_taker_contracts: float = 0.0
    large_yes_contracts: float = 0.0
    large_no_contracts: float = 0.0
    trade_imbalance: float = 0.0
    large_trade_imbalance: float = 0.0
    book_imbalance: float | None = None
    price_momentum: float | None = None
    score: float = 0.0
    probability_adjustment: float = 0.0
    reason: str = "disabled"

    @classmethod
    def disabled(cls) -> "OrderflowSummary":
        return cls(enabled=False)

    @classmethod
    def unavailable(cls, reason: str) -> "OrderflowSummary":
        return cls(enabled=True, reason=reason)

    def note(self) -> str:
        return (
            f"orderflow_trades={self.trade_count} orderflow_contracts={self.contract_volume:.2f} "
            f"orderflow_imb={self.trade_imbalance:.4f} large_orderflow_imb={self.large_trade_imbalance:.4f} "
            f"book_imb={_fmt_optional(self.book_imbalance)} orderflow_momentum={_fmt_optional(self.price_momentum)} "
            f"orderflow_score={self.score:.4f} orderflow_adj={self.probability_adjustment:.4f} "
            f"orderflow_reason={self.reason}"
        )


class OrderflowAnalyzer:
    def __init__(self, client: Any, config: OrderflowConfig) -> None:
        self.client = client
        self.config = config
        self._cache: dict[tuple[str, int], OrderflowSummary] = {}
        self._prefetch_min_ts: int | None = None
        self._prefetched_all_trades: list[dict[str, Any]] = []
        self._prefetched_trades: dict[str, list[dict[str, Any]]] = {}

    def prefetch(self, tickers: list[str], *, now: datetime) -> None:
        if not self.config.enabled or not tickers or not hasattr(self.client, "list_trades"):
            return
        min_ts = int((now - timedelta(minutes=self.config.lookback_minutes)).timestamp())
        if self._prefetch_min_ts == min_ts:
            self._group_prefetched_tickers(tickers)
            return
        try:
            trades, _cursor = self.client.list_trades(min_ts=min_ts, limit=1000)
        except KalshiApiError:
            return
        self._prefetch_min_ts = min_ts
        self._prefetched_all_trades = trades
        self._prefetched_trades = {}
        self._group_prefetched_tickers(tickers)

    def _group_prefetched_tickers(self, tickers: list[str]) -> None:
        ticker_set = set(tickers)
        missing_tickers = ticker_set.difference(self._prefetched_trades)
        if not missing_tickers:
            return
        grouped = {ticker: [] for ticker in missing_tickers}
        for trade in self._prefetched_all_trades:
            ticker = str(trade.get("ticker") or "")
            if ticker in grouped:
                grouped[ticker].append(trade)
        self._prefetched_trades.update(grouped)

    def summarize(self, ticker: str, *, top: TopOfBook, now: datetime) -> OrderflowSummary:
        if not self.config.enabled:
            return OrderflowSummary.disabled()
        min_ts = int((now - timedelta(minutes=self.config.lookback_minutes)).timestamp())
        cache_key = (ticker, min_ts)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        summary = self._summarize_uncached(ticker, top=top, min_ts=min_ts)
        self._cache[cache_key] = summary
        return summary

    def _summarize_uncached(self, ticker: str, *, top: TopOfBook, min_ts: int) -> OrderflowSummary:
        if not hasattr(self.client, "list_trades"):
            return OrderflowSummary.unavailable("client_has_no_trade_endpoint")
        if self._prefetch_min_ts == min_ts and ticker in self._prefetched_trades:
            return summarize_orderflow(self._prefetched_trades[ticker], top=top, config=self.config)
        try:
            trades, _cursor = self.client.list_trades(ticker=ticker, min_ts=min_ts, limit=1000)
        except KalshiApiError as exc:
            return OrderflowSummary.unavailable(f"trade_fetch_error:{_compact(str(exc))}")
        return summarize_orderflow(trades, top=top, config=self.config)


def summarize_orderflow(
    trades: list[dict[str, Any]],
    *,
    top: TopOfBook,
    config: OrderflowConfig,
) -> OrderflowSummary:
    parsed = [_parse_trade(raw) for raw in trades]
    parsed = [trade for trade in parsed if trade is not None]
    trade_count = len(parsed)
    yes_contracts = sum(trade["count"] for trade in parsed if trade["side"] == "yes")
    no_contracts = sum(trade["count"] for trade in parsed if trade["side"] == "no")
    contract_volume = yes_contracts + no_contracts
    large_yes = sum(
        trade["count"] for trade in parsed if trade["side"] == "yes" and trade["count"] >= config.large_trade_contracts
    )
    large_no = sum(
        trade["count"] for trade in parsed if trade["side"] == "no" and trade["count"] >= config.large_trade_contracts
    )
    trade_imbalance = _signed_imbalance(yes_contracts, no_contracts)
    large_imbalance = _signed_imbalance(large_yes, large_no)
    book_imbalance = _book_imbalance(top)
    price_momentum = _price_momentum(parsed)

    enough_flow = trade_count >= config.min_trades and contract_volume >= config.min_contracts
    if not enough_flow:
        return OrderflowSummary(
            enabled=True,
            trade_count=trade_count,
            contract_volume=round(contract_volume, 4),
            yes_taker_contracts=round(yes_contracts, 4),
            no_taker_contracts=round(no_contracts, 4),
            large_yes_contracts=round(large_yes, 4),
            large_no_contracts=round(large_no, 4),
            trade_imbalance=round(trade_imbalance, 4),
            large_trade_imbalance=round(large_imbalance, 4),
            book_imbalance=book_imbalance,
            price_momentum=price_momentum,
            reason="insufficient_recent_flow",
        )

    momentum_score = _clamp((price_momentum or 0.0) / 0.05, -1.0, 1.0)
    score = (
        config.trade_weight * trade_imbalance
        + config.large_trade_weight * large_imbalance
        + config.book_weight * (book_imbalance or 0.0)
        + config.momentum_weight * momentum_score
    )
    score = _clamp(score, -1.0, 1.0)
    adjustment = _clamp(score * config.max_probability_adjustment, -config.max_probability_adjustment, config.max_probability_adjustment)
    return OrderflowSummary(
        enabled=True,
        trade_count=trade_count,
        contract_volume=round(contract_volume, 4),
        yes_taker_contracts=round(yes_contracts, 4),
        no_taker_contracts=round(no_contracts, 4),
        large_yes_contracts=round(large_yes, 4),
        large_no_contracts=round(large_no, 4),
        trade_imbalance=round(trade_imbalance, 4),
        large_trade_imbalance=round(large_imbalance, 4),
        book_imbalance=book_imbalance,
        price_momentum=price_momentum,
        score=round(score, 4),
        probability_adjustment=round(adjustment, 4),
        reason="recent_taker_and_book_pressure",
    )


def _parse_trade(raw: dict[str, Any]) -> dict[str, Any] | None:
    side = _trade_side(raw)
    count = _optional_float(raw.get("count_fp", raw.get("count")))
    price = _yes_price(raw)
    created_at = _parse_time(raw.get("created_time"))
    if side not in {"yes", "no"} or count is None or count <= 0:
        return None
    return {"side": side, "count": count, "yes_price": price, "created_at": created_at}


def _trade_side(raw: dict[str, Any]) -> str | None:
    for key in ("taker_outcome_side", "taker_side"):
        value = raw.get(key)
        if isinstance(value, str) and value.lower() in {"yes", "no"}:
            return value.lower()
    book_side = raw.get("taker_book_side")
    if isinstance(book_side, str):
        if book_side.lower() == "bid":
            return "yes"
        if book_side.lower() == "ask":
            return "no"
    return None


def _yes_price(raw: dict[str, Any]) -> float | None:
    yes_price = _optional_float(raw.get("yes_price_dollars", raw.get("yes_price")))
    if yes_price is not None:
        return yes_price
    no_price = _optional_float(raw.get("no_price_dollars", raw.get("no_price")))
    return None if no_price is None else round(1.0 - no_price, 4)


def _price_momentum(parsed: list[dict[str, Any]]) -> float | None:
    priced = [trade for trade in parsed if trade["yes_price"] is not None and trade["created_at"] is not None]
    if len(priced) < 2:
        return None
    priced = sorted(priced, key=lambda trade: trade["created_at"])
    return round(float(priced[-1]["yes_price"]) - float(priced[0]["yes_price"]), 4)


def _book_imbalance(top: TopOfBook) -> float | None:
    yes_size = max(float(top.yes_bid_size or 0.0), 0.0)
    no_size = max(float(top.no_bid_size or 0.0), 0.0)
    if yes_size + no_size <= 0:
        return None
    return round(_signed_imbalance(yes_size, no_size), 4)


def _signed_imbalance(yes_value: float, no_value: float) -> float:
    total = yes_value + no_value
    if total <= 0:
        return 0.0
    return _clamp((yes_value - no_value) / total, -1.0, 1.0)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(float(value), lower), upper)


def _fmt_optional(value: float | None) -> str:
    return "none" if value is None else f"{value:.4f}"


def _compact(value: str) -> str:
    return value.replace(" ", "_").replace(",", "")[:80]
