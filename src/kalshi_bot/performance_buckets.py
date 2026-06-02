from __future__ import annotations

from collections.abc import Mapping

from .models import Signal


def signal_performance_bucket_keys(signal: Signal) -> tuple[str, ...]:
    asset = _asset_bucket(signal.asset, signal.ticker)
    side = signal.outcome.value
    horizon = horizon_bucket(signal.time_to_close_minutes)
    spread = spread_bucket(signal.spread)
    return _bucket_keys(asset=asset, side=side, horizon=horizon, spread=spread)


def row_performance_bucket_keys(row: Mapping[str, object]) -> tuple[str, ...]:
    asset = _asset_bucket(row.get("asset"), row.get("ticker"))
    side = str(row.get("outcome") or "").lower()
    horizon = horizon_bucket(row.get("time_to_close_minutes"))
    spread = spread_bucket(row.get("spread"))
    return _bucket_keys(asset=asset, side=side, horizon=horizon, spread=spread)


def horizon_bucket(value: object) -> str:
    minutes = _float(value)
    if minutes is None:
        return "unknown"
    if minutes <= 10:
        return "<=10m"
    if minutes <= 30:
        return "10-30m"
    if minutes <= 60:
        return "30-60m"
    if minutes <= 240:
        return "1-4h"
    if minutes <= 1440:
        return "4-24h"
    return ">24h"


def spread_bucket(value: object) -> str:
    spread = _float(value)
    if spread is None:
        return "unknown"
    if spread <= 0.02:
        return "<=2c"
    if spread <= 0.05:
        return "2-5c"
    if spread <= 0.10:
        return "5-10c"
    return ">10c"


def _bucket_keys(*, asset: str, side: str, horizon: str, spread: str) -> tuple[str, ...]:
    if not asset:
        return ()
    return (
        f"asset_side:{asset}:{side}",
        f"asset_side_horizon:{asset}:{side}:{horizon}",
        f"asset_side_spread:{asset}:{side}:{spread}",
    )


def _asset_bucket(asset: object, ticker: object) -> str:
    value = str(asset or "").upper()
    if value:
        return value
    return _asset_from_ticker(str(ticker or ""))


def _asset_from_ticker(ticker: str) -> str:
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
    return ""


def _float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
