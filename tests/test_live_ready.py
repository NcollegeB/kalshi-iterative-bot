from argparse import Namespace

import pytest

from kalshi_bot.cli import _balance_dollars, _resolve_mode, _scan_args_for_loop, _take_profit_args_for_loop, run_loop
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
