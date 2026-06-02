from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class TradeMode(str, Enum):
    PAPER = "paper"
    DEMO = "demo"
    LIVE = "live"


class OutcomeSide(str, Enum):
    YES = "yes"
    NO = "no"


class BookSide(str, Enum):
    BID = "bid"
    ASK = "ask"


@dataclass(frozen=True)
class Market:
    ticker: str
    title: str
    event_ticker: str | None
    status: str
    volume: float
    yes_bid: float | None
    no_bid: float | None
    close_time: str | None
    settlement_value: float | None
    settlement_ts: str | None
    category: str | None

    @classmethod
    def from_api(cls, raw: dict) -> "Market":
        return cls(
            ticker=str(raw.get("ticker", "")),
            title=str(raw.get("title", "")),
            event_ticker=raw.get("event_ticker"),
            status=str(raw.get("status", "")),
            volume=_as_float(raw.get("volume_fp", raw.get("volume", 0))),
            yes_bid=_optional_float(raw.get("yes_bid_dollars", raw.get("yes_bid"))),
            no_bid=_optional_float(raw.get("no_bid_dollars", raw.get("no_bid"))),
            close_time=raw.get("close_time"),
            settlement_value=_optional_float(raw.get("settlement_value_dollars")),
            settlement_ts=raw.get("settlement_ts"),
            category=raw.get("category"),
        )


@dataclass(frozen=True)
class TopOfBook:
    yes_bid: float | None
    yes_bid_size: float
    no_bid: float | None
    no_bid_size: float

    @property
    def yes_ask(self) -> float | None:
        return None if self.no_bid is None else round(1.0 - self.no_bid, 4)

    @property
    def no_ask(self) -> float | None:
        return None if self.yes_bid is None else round(1.0 - self.yes_bid, 4)

    @property
    def yes_spread(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return round(self.yes_ask - self.yes_bid, 4)


@dataclass(frozen=True)
class Signal:
    strategy: str
    ticker: str
    market_title: str
    outcome: OutcomeSide
    estimated_probability: float
    reference_price: float
    edge: float
    reason: str
    created_at: datetime
    asset: str | None = None
    model_probability_yes: float | None = None
    kalshi_yes_ask: float | None = None
    kalshi_no_ask: float | None = None
    spread: float | None = None
    time_to_close_minutes: float | None = None
    annual_volatility: float | None = None
    momentum_6h: float | None = None
    raw_probability_yes: float | None = None
    raw_edge: float | None = None

    @classmethod
    def now(
        cls,
        *,
        strategy: str,
        ticker: str,
        market_title: str,
        outcome: OutcomeSide,
        estimated_probability: float,
        reference_price: float,
        edge: float,
        reason: str,
        asset: str | None = None,
        model_probability_yes: float | None = None,
        kalshi_yes_ask: float | None = None,
        kalshi_no_ask: float | None = None,
        spread: float | None = None,
        time_to_close_minutes: float | None = None,
        annual_volatility: float | None = None,
        momentum_6h: float | None = None,
        raw_probability_yes: float | None = None,
        raw_edge: float | None = None,
    ) -> "Signal":
        return cls(
            strategy=strategy,
            ticker=ticker,
            market_title=market_title,
            outcome=outcome,
            estimated_probability=estimated_probability,
            reference_price=reference_price,
            edge=edge,
            reason=reason,
            created_at=datetime.now(timezone.utc),
            asset=asset,
            model_probability_yes=model_probability_yes,
            kalshi_yes_ask=kalshi_yes_ask,
            kalshi_no_ask=kalshi_no_ask,
            spread=spread,
            time_to_close_minutes=time_to_close_minutes,
            annual_volatility=annual_volatility,
            momentum_6h=momentum_6h,
            raw_probability_yes=raw_probability_yes,
            raw_edge=raw_edge,
        )


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    count: float = 0.0
    max_loss_dollars: float = 0.0
    net_edge_after_fees: float | None = None
    fee_haircut_dollars: float | None = None
    raw_edge_dollars: float | None = None


@dataclass(frozen=True)
class ProposedOrder:
    ticker: str
    book_side: BookSide
    outcome: OutcomeSide
    count: float
    price: float
    client_order_id: str
    post_only: bool = True
    reduce_only: bool = False
    time_in_force: str = "good_till_canceled"


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return _as_float(value)


def _as_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
