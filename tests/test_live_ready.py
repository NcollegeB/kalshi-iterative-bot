from argparse import Namespace

import pytest

from kalshi_bot.cli import (
    _allowed_refresh_assets,
    _balance_dollars,
    _resolve_mode,
    _scan_args_for_loop,
    _take_profit_args_for_loop,
    run_loop,
    run_refresh_btc,
)
from kalshi_bot.models import TradeMode


def test_balance_dollars_prefers_explicit_field():
    assert _balance_dollars({"balance": 20, "balance_dollars": "0.2000"}) == 0.2


def test_balance_dollars_falls_back_to_cents():
    assert _balance_dollars({"balance": 250}) == 2.5


def test_dry_run_live_mode_does_not_require_opt_in():
    args = Namespace(live=True, demo=False, dry_run=True)
    assert _resolve_mode(args, "production") == TradeMode.LIVE


def test_loop_rejects_btc_and_crypto_refresh_together():
    args = Namespace(interval_seconds=1, iterations=1, refresh_btc=True, refresh_crypto=True)
    with pytest.raises(SystemExit):
        run_loop(args, None, None, None)


def test_loop_dry_runs_live_actions_when_trading_is_paused():
    args = Namespace(
        probability_file="data/probabilities.csv",
        limit=100,
        pages=1,
        skip_pages=0,
        include_multivariate=False,
        enable_live_buys=True,
        execute_exits=True,
        profit_pct=100.0,
        min_profit_cents=0.0,
    )

    assert _scan_args_for_loop(args, trading_active=False).dry_run is True
    assert _take_profit_args_for_loop(args, trading_active=False).execute is False


def test_allowed_refresh_assets_respects_risk_whitelist():
    assets = _allowed_refresh_assets(["BTC", "ETH", "XRP", "DOGE"], ("BTC", "ETH", "SOL"))
    assert assets == ["BTC", "ETH"]


def test_allowed_refresh_assets_rejects_empty_intersection():
    with pytest.raises(SystemExit, match="BOT_ALLOWED_ASSETS"):
        _allowed_refresh_assets(["XRP", "DOGE"], ("BTC", "ETH", "SOL"))


def test_refresh_btc_clears_probability_file_on_market_data_error(tmp_path, monkeypatch):
    output = tmp_path / "probabilities.csv"
    args = Namespace(
        spot=None,
        output=output,
        series="KXBTCD",
        limit=100,
        pages=1,
        annual_vol=0.55,
        probability_shrink=0.75,
        max_rows=12,
        dry_run=False,
    )
    monkeypatch.setattr("kalshi_bot.cli.current_coinbase_btc_spot", lambda: (_ for _ in ()).throw(RuntimeError("down")))

    assert run_refresh_btc(args, None, None) == 0
    assert output.read_text() == "ticker,estimated_probability,notes\n"
