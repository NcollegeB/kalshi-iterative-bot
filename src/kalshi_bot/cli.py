from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adaptive_risk import evaluate_adaptive_risk
from .btc_model import current_coinbase_btc_spot, generate_btc_probability_rows, write_probability_csv
from .config import DATA_DIR, load_config
from .crypto_model import DEFAULT_ASSETS, generate_crypto_probability_rows, write_crypto_probability_csv
from .dashboard import performance_breakdown, serve_dashboard
from .kalshi_client import KalshiApiError, KalshiClient
from .ledger import PaperLedger
from .model_learning import evaluate_asset_performance_guard, evaluate_bucket_performance_guard, load_asset_calibration
from .models import BookSide, OutcomeSide, ProposedOrder, TradeMode
from .risk import PortfolioState, RiskManager
from .settlement import calculate_paper_settlement
from .simulation import random_search, simulate, SimulationParams
from .strategies.probability_file import ProbabilityFileStrategy
from .strategies.spread_maker import SpreadMakerPaperStrategy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kalshi iterative trading bot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan markets and record paper/demo/live candidate orders")
    scan_parser.add_argument("--strategy", choices=["spread-maker", "probability-file"], default="spread-maker")
    scan_parser.add_argument("--probability-file", type=Path, default=DATA_DIR / "probabilities.csv")
    scan_parser.add_argument("--limit", type=int, default=100)
    scan_parser.add_argument("--pages", type=int, default=1)
    scan_parser.add_argument("--skip-pages", type=int, default=0)
    scan_parser.add_argument("--min-volume", type=float, default=100.0)
    scan_parser.add_argument("--workers", type=int, default=6)
    scan_parser.add_argument("--include-multivariate", action="store_true")
    scan_parser.add_argument("--live", action="store_true", help="Place real production orders if env opt-in is set")
    scan_parser.add_argument("--demo", action="store_true", help="Place demo orders if credentials point at demo")
    scan_parser.add_argument("--dry-run", action="store_true", help="Evaluate signals without recording or submitting orders")

    loop_parser = subparsers.add_parser("loop", help="Continuously check exits and probability-file entries")
    loop_parser.add_argument("--interval-seconds", type=float, default=60.0)
    loop_parser.add_argument("--iterations", type=int, default=0, help="0 means run until stopped")
    loop_parser.add_argument("--probability-file", type=Path, default=DATA_DIR / "probabilities.csv")
    loop_parser.add_argument("--limit", type=int, default=100)
    loop_parser.add_argument("--pages", type=int, default=1)
    loop_parser.add_argument("--skip-pages", type=int, default=0)
    loop_parser.add_argument("--include-multivariate", action="store_true")
    loop_parser.add_argument("--profit-pct", type=float, default=100.0)
    loop_parser.add_argument("--min-profit-cents", type=float, default=0.0)
    loop_parser.add_argument("--execute-exits", action="store_true", help="Submit live reduce-only exit orders")
    loop_parser.add_argument("--enable-live-buys", action="store_true", help="Submit live probability-file buy orders")
    loop_parser.add_argument("--refresh-btc", action="store_true", help="Refresh BTC probability rows each loop")
    loop_parser.add_argument("--btc-series", default="KXBTCD")
    loop_parser.add_argument("--btc-annual-vol", type=float, default=0.55)
    loop_parser.add_argument("--btc-probability-shrink", type=float, default=0.75)
    loop_parser.add_argument("--btc-max-rows", type=int, default=12)
    loop_parser.add_argument("--refresh-crypto", action="store_true", help="Refresh multi-asset crypto rows each loop")
    loop_parser.add_argument("--crypto-assets", default=",".join(DEFAULT_ASSETS))
    loop_parser.add_argument("--crypto-max-rows-per-asset", type=int, default=4)
    loop_parser.add_argument("--crypto-probability-shrink", type=float, default=0.70)
    loop_parser.add_argument("--crypto-market-blend", type=float, default=0.15)
    loop_parser.add_argument("--crypto-max-model-market-gap", type=float, default=0.35)
    loop_parser.add_argument("--crypto-min-annual-vol", type=float, default=0.0)
    loop_parser.add_argument("--crypto-max-annual-vol", type=float, default=1.75)

    refresh_btc_parser = subparsers.add_parser("refresh-btc", help="Write current BTC model rows to probabilities.csv")
    refresh_btc_parser.add_argument("--output", type=Path, default=DATA_DIR / "probabilities.csv")
    refresh_btc_parser.add_argument("--series", default="KXBTCD")
    refresh_btc_parser.add_argument("--limit", type=int, default=100)
    refresh_btc_parser.add_argument("--pages", type=int, default=1)
    refresh_btc_parser.add_argument("--annual-vol", type=float, default=0.55)
    refresh_btc_parser.add_argument("--probability-shrink", type=float, default=0.75)
    refresh_btc_parser.add_argument("--market-blend", type=float, default=0.15)
    refresh_btc_parser.add_argument("--max-model-market-gap", type=float, default=0.35)
    refresh_btc_parser.add_argument("--max-rows", type=int, default=12)
    refresh_btc_parser.add_argument("--spot", type=float, default=None)
    refresh_btc_parser.add_argument("--dry-run", action="store_true")

    refresh_crypto_parser = subparsers.add_parser(
        "refresh-crypto",
        help="Write current BTC/ETH/SOL/XRP/DOGE model rows to a separate probability file",
    )
    refresh_crypto_parser.add_argument("--output", type=Path, default=DATA_DIR / "crypto_probabilities.csv")
    refresh_crypto_parser.add_argument("--assets", default=",".join(DEFAULT_ASSETS))
    refresh_crypto_parser.add_argument("--limit", type=int, default=100)
    refresh_crypto_parser.add_argument("--pages", type=int, default=1)
    refresh_crypto_parser.add_argument("--probability-shrink", type=float, default=0.70)
    refresh_crypto_parser.add_argument("--market-blend", type=float, default=0.15)
    refresh_crypto_parser.add_argument("--max-model-market-gap", type=float, default=0.35)
    refresh_crypto_parser.add_argument("--min-annual-vol", type=float, default=0.0)
    refresh_crypto_parser.add_argument("--max-annual-vol", type=float, default=1.75)
    refresh_crypto_parser.add_argument("--max-rows-per-asset", type=int, default=4)
    refresh_crypto_parser.add_argument("--dry-run", action="store_true")

    optimize_parser = subparsers.add_parser("optimize", help="Run Monte Carlo parameter search over logged signals")
    optimize_parser.add_argument("--search-trials", type=int, default=1000)
    optimize_parser.add_argument("--mc-trials", type=int, default=1000)
    optimize_parser.add_argument("--top", type=int, default=10)
    optimize_parser.add_argument("--seed", type=int, default=1)
    optimize_parser.add_argument("--include-multivariate", action="store_true")

    reconcile_parser = subparsers.add_parser("reconcile", help="Settle paper orders whose markets have resolved")
    reconcile_parser.add_argument("--limit", type=int, default=None)
    reconcile_parser.add_argument("--dry-run", action="store_true")
    reconcile_parser.add_argument("--no-fee-estimate", action="store_true")
    reconcile_live_parser = subparsers.add_parser("reconcile-live", help="Mark local live orders settled from Kalshi settlements")
    reconcile_live_parser.add_argument("--limit", type=int, default=100)
    reconcile_live_parser.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("sync-live-orders", help="Refresh local live order fill counts from Kalshi")
    subparsers.add_parser("sync-live-exits", help="Refresh local take-profit exits from Kalshi fills")
    cancel_live_resting_parser = subparsers.add_parser(
        "cancel-live-resting",
        help="Cancel remaining resting live orders and sync local state",
    )
    cancel_live_resting_parser.add_argument("--dry-run", action="store_true")

    subparsers.add_parser("live-ready", help="Check credentials, exchange status, and live-trading gates")
    subparsers.add_parser("balance", help="Fetch authenticated Kalshi balance")
    settlements_parser = subparsers.add_parser("portfolio-settlements", help="Fetch authenticated Kalshi settlements")
    settlements_parser.add_argument("--limit", type=int, default=20)
    settlements_parser.add_argument("--ticker", default=None)
    settlements_parser.add_argument("--event-ticker", default=None)
    take_profit_parser = subparsers.add_parser("take-profit", help="Create reduce-only exit limit orders for live positions")
    take_profit_parser.add_argument("--profit-pct", type=float, default=100.0)
    take_profit_parser.add_argument("--min-profit-cents", type=float, default=0.0)
    take_profit_parser.add_argument("--execute", action="store_true")
    subparsers.add_parser("status", help="Show local paper ledger summary")
    performance_report_parser = subparsers.add_parser("performance-report", help="Show realized PnL/calibration buckets")
    performance_report_parser.add_argument("--mode", choices=["all", "paper", "demo", "live"], default="all")
    subparsers.add_parser("health", help="Check public Kalshi API status")
    dashboard_parser = subparsers.add_parser("dashboard", help="Run a read-only local dashboard")
    dashboard_parser.add_argument("--host", default="127.0.0.1")
    dashboard_parser.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)
    config = load_config()
    client = KalshiClient(config.kalshi)
    ledger = PaperLedger(config.db_path)

    if args.command == "health":
        status = client.get_exchange_status()
        print(status)
        return 0

    if args.command == "status":
        print(ledger.summary())
        return 0

    if args.command == "performance-report":
        mode_filter = None if args.mode == "all" else args.mode
        print(json.dumps(performance_breakdown(config.db_path, mode=mode_filter), indent=2))
        return 0

    if args.command == "scan":
        return run_scan(args, config, client, ledger)

    if args.command == "loop":
        return run_loop(args, config, client, ledger)

    if args.command == "refresh-btc":
        return run_refresh_btc(args, config, client)

    if args.command == "refresh-crypto":
        return run_refresh_crypto(args, config, client)

    if args.command == "optimize":
        return run_optimize(args, config, ledger)

    if args.command == "reconcile":
        return run_reconcile(args, client, ledger)

    if args.command == "reconcile-live":
        return run_reconcile_live(args, client, ledger)

    if args.command == "sync-live-orders":
        return run_sync_live_orders(client, ledger)

    if args.command == "sync-live-exits":
        return run_sync_live_exits(client, ledger)

    if args.command == "cancel-live-resting":
        return run_cancel_live_resting(args, config, client, ledger)

    if args.command == "live-ready":
        return run_live_ready(config, client, ledger)

    if args.command == "balance":
        print(client.get_balance())
        return 0

    if args.command == "portfolio-settlements":
        settlements, cursor = client.list_settlements(
            limit=args.limit,
            ticker=args.ticker,
            event_ticker=args.event_ticker,
        )
        print({"settlements": settlements, "cursor": cursor})
        return 0

    if args.command == "take-profit":
        return run_take_profit(args, config, client, ledger)

    if args.command == "dashboard":
        serve_dashboard(config, host=args.host, port=args.port)
        return 0

    parser.error("unknown command")
    return 2


def run_scan(args: argparse.Namespace, config, client: KalshiClient, ledger: PaperLedger) -> int:
    mode = _resolve_mode(args, config.kalshi.environment)
    markets = _fetch_markets(
        client,
        limit=args.limit,
        pages=args.pages,
        skip_pages=args.skip_pages,
        exclude_multivariate=not args.include_multivariate,
    )
    if args.strategy == "probability-file":
        strategy = ProbabilityFileStrategy(args.probability_file, config.risk.min_edge_dollars)
    else:
        strategy = SpreadMakerPaperStrategy(
            min_volume=args.min_volume,
            exclude_multivariate=not args.include_multivariate,
            workers=args.workers,
        )
        if mode != TradeMode.PAPER:
            raise SystemExit("spread-maker is paper-only; use probability-file for demo/live execution")

    signals = strategy.generate(client, markets)
    adaptive_report = _adaptive_risk_for_mode(config, ledger, mode)
    asset_performance_guard = _asset_performance_guard_for_mode(config, ledger, mode)
    bucket_performance_guard = _bucket_performance_guard_for_mode(config, ledger, mode)
    blocked_assets = [asset for asset, report in asset_performance_guard.items() if report.blocked]
    blocked_buckets = {
        bucket_key: report.reason for bucket_key, report in bucket_performance_guard.items() if report.blocked
    }
    risk = RiskManager(
        config.risk,
        risk_multiplier=adaptive_report.multiplier,
        blocked_assets=blocked_assets,
        blocked_buckets=blocked_buckets,
    )
    state = PortfolioState(
        bankroll_dollars=config.risk.bankroll_dollars,
        open_risk_dollars=ledger.open_risk(mode),
        realized_pnl_today_dollars=ledger.realized_pnl_today(mode),
    )

    submitted = 0
    rejected = 0
    order_errors = 0
    order_error_examples = []
    for signal in signals:
        decision = risk.evaluate(signal, state)
        approved = decision.approved
        decision_reason = decision.reason
        if approved and mode == TradeMode.LIVE and ledger.has_live_exposure(signal.ticker):
            approved = False
            decision_reason = "existing live exposure for ticker"

        signal_id = 0
        if not getattr(args, "dry_run", False):
            signal_id = ledger.record_signal(
                signal,
                mode,
                status="approved" if approved else "rejected",
                risk_reason=decision_reason,
            )
        if not approved:
            rejected += 1
            continue

        order = _order_from_signal(client, signal, decision.count)
        if getattr(args, "dry_run", False):
            pass
        elif mode == TradeMode.PAPER:
            ledger.record_order(signal_id, order, mode, "paper_open")
        else:
            try:
                response = client.create_event_order_v2(order)
            except KalshiApiError as exc:
                order_errors += 1
                rejected += 1
                if len(order_error_examples) < 8:
                    order_error_examples.append(
                        {
                            "ticker": signal.ticker,
                            "outcome": signal.outcome.value,
                            "price": order.price,
                            "reason": str(exc)[:180],
                        }
                    )
                continue
            ledger.record_order(
                signal_id,
                order,
                mode,
                _order_status_from_response(mode, response),
                exchange_response=response,
            )
        submitted += 1
        state = PortfolioState(
            bankroll_dollars=state.bankroll_dollars,
            open_risk_dollars=state.open_risk_dollars + decision.max_loss_dollars,
            realized_pnl_today_dollars=state.realized_pnl_today_dollars,
        )

    print(
        {
            "mode": mode.value,
            "markets_scanned": len(markets),
            "skip_pages": args.skip_pages,
            "signals": len(signals),
            "orders_recorded": 0 if getattr(args, "dry_run", False) else submitted,
            "orders_approved": submitted,
            "signals_rejected": rejected,
            "order_errors": order_errors,
            "order_error_examples": order_error_examples,
            "adaptive_risk": adaptive_report.to_dict(),
            "performance_guard": {
                "enabled": config.risk.performance_guard_enabled and mode == TradeMode.LIVE,
                "blocked_assets": blocked_assets,
                "blocked_buckets": sorted(blocked_buckets),
                "assets": {asset: report.to_dict() for asset, report in asset_performance_guard.items()},
                "buckets": {bucket_key: report.to_dict() for bucket_key, report in bucket_performance_guard.items()},
            },
            "dry_run": getattr(args, "dry_run", False),
            "db_path": str(config.db_path),
        }
    )
    return 0


def run_loop(args: argparse.Namespace, config, client: KalshiClient, ledger: PaperLedger) -> int:
    if args.interval_seconds <= 0:
        raise SystemExit("--interval-seconds must be greater than 0")
    if args.iterations < 0:
        raise SystemExit("--iterations must be 0 or greater")
    if args.refresh_btc and args.refresh_crypto:
        raise SystemExit("Use only one of --refresh-btc or --refresh-crypto for a loop.")

    live_actions_enabled = args.enable_live_buys or args.execute_exits
    if live_actions_enabled:
        startup = _live_loop_startup_ready(config, client, ledger)
        print({"live_loop_startup": startup}, flush=True)
        if not startup["ready"]:
            return 1

    iteration = 0
    try:
        while args.iterations == 0 or iteration < args.iterations:
            iteration += 1
            trading_active = True
            exchange_status = None
            if live_actions_enabled:
                try:
                    exchange_status = client.get_exchange_status()
                    trading_active = bool(exchange_status.get("trading_active"))
                except KalshiApiError as exc:
                    trading_active = False
                    print({"loop_iteration": iteration, "exchange_status_error": str(exc)}, file=sys.stderr, flush=True)
            print(
                {
                    "loop_iteration": iteration,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "live_buys_enabled": args.enable_live_buys,
                    "execute_exits": args.execute_exits,
                    "trading_active": trading_active,
                },
                flush=True,
            )
            if live_actions_enabled and not trading_active:
                print(
                    {
                        "loop_iteration": iteration,
                        "live_execution_paused": True,
                        "reason": "Kalshi trading_active is false; syncing and refreshing only.",
                        "exchange_status": exchange_status,
                    },
                    flush=True,
                )

            try:
                run_sync_live_orders(client, ledger)
                run_sync_live_exits(client, ledger)
                if config.kalshi.has_credentials:
                    run_reconcile_live(_reconcile_live_args_for_loop(), client, ledger)
                if args.refresh_btc:
                    run_refresh_btc(_refresh_btc_args_for_loop(args), config, client)
                if args.refresh_crypto:
                    run_refresh_crypto(_refresh_crypto_args_for_loop(args), config, client)
                run_take_profit(_take_profit_args_for_loop(args, trading_active=trading_active), config, client, ledger)
                run_sync_live_exits(client, ledger)
                run_scan(_scan_args_for_loop(args, trading_active=trading_active), config, client, ledger)
            except KalshiApiError as exc:
                print({"loop_iteration": iteration, "kalshi_api_error": str(exc)}, file=sys.stderr, flush=True)

            if args.iterations and iteration >= args.iterations:
                break
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        print({"loop_stopped": "keyboard_interrupt", "iterations": iteration}, flush=True)
        return 130

    print({"loop_complete": True, "iterations": iteration}, flush=True)
    return 0


def _live_loop_startup_ready(config, client: KalshiClient, ledger: PaperLedger | None = None) -> dict[str, Any]:
    checks = {
        "environment_is_production": config.kalshi.environment == "production",
        "credentials_configured": config.kalshi.has_credentials,
        "live_opt_in": config.kalshi.allow_live == "I_ACCEPT_KALSHI_LIVE_RISK",
    }
    try:
        exchange_status = client.get_exchange_status()
        checks["exchange_active"] = bool(exchange_status.get("exchange_active"))
        checks["trading_active"] = bool(exchange_status.get("trading_active"))
    except KalshiApiError as exc:
        exchange_status = {"error": str(exc)}
        checks["exchange_active"] = False
        checks["trading_active"] = False

    balance = None
    available_balance_dollars = 0.0
    if checks["credentials_configured"]:
        try:
            balance = client.get_balance()
            available_balance_dollars = _balance_dollars(balance)
            checks["balance_fetch_ok"] = True
        except KalshiApiError as exc:
            balance = {"error": str(exc)}
            checks["balance_fetch_ok"] = False
    else:
        checks["balance_fetch_ok"] = False
    adaptive_report = _adaptive_risk_for_mode(config, ledger, TradeMode.LIVE) if ledger is not None else None
    effective_max_position = (
        adaptive_report.effective_max_position_dollars if adaptive_report is not None else config.risk.max_position_dollars
    )
    checks["balance_covers_max_position"] = available_balance_dollars >= effective_max_position

    required_checks = {key: value for key, value in checks.items() if key != "trading_active"}
    return {
        "ready": all(required_checks.values()),
        "checks": checks,
        "trading_active_required_to_submit": True,
        "exchange_status": exchange_status,
        "balance": balance,
        "available_balance_dollars": available_balance_dollars,
        "adaptive_risk": adaptive_report.to_dict() if adaptive_report is not None else None,
    }


def run_refresh_btc(args: argparse.Namespace, config, client: KalshiClient) -> int:
    try:
        spot = args.spot if args.spot is not None else current_coinbase_btc_spot()
    except RuntimeError as exc:
        if not args.dry_run:
            write_probability_csv(args.output, [])
        print(
            {
                "dry_run": args.dry_run,
                "output": str(args.output),
                "series": args.series,
                "rows": 0,
                "market_data_error": str(exc),
            },
            flush=True,
        )
        return 0
    rows = generate_btc_probability_rows(
        client,
        spot=spot,
        now=datetime.now(timezone.utc),
        series_ticker=args.series,
        limit=args.limit,
        pages=args.pages,
        annual_volatility=args.annual_vol,
        probability_shrink=args.probability_shrink,
        market_blend=getattr(args, "market_blend", 0.15),
        max_model_market_gap=getattr(args, "max_model_market_gap", 0.35),
        min_edge=config.risk.min_edge_dollars,
        max_rows=args.max_rows,
        max_spread=config.risk.max_spread_dollars,
        min_horizon_minutes=config.risk.min_time_to_close_minutes,
        max_horizon_minutes=config.risk.max_time_to_close_minutes,
    )
    if not args.dry_run:
        write_probability_csv(args.output, rows)
    print(
        {
            "dry_run": args.dry_run,
            "output": str(args.output),
            "series": args.series,
            "spot": round(float(spot), 2),
            "market_blend": getattr(args, "market_blend", 0.15),
            "max_model_market_gap": getattr(args, "max_model_market_gap", 0.35),
            "rows": len(rows),
            "top": [
                {
                    "ticker": row.ticker,
                    "side": row.best_side,
                    "edge": row.best_edge,
                    "p_yes": row.estimated_probability,
                    "strike": row.strike,
                    "horizon_min": row.horizon_minutes,
                    "yes_ask": row.yes_ask,
                    "no_ask": row.no_ask,
                }
                for row in rows[:5]
            ],
        }
    )
    return 0


def run_refresh_crypto(args: argparse.Namespace, config, client: KalshiClient) -> int:
    assets = _allowed_refresh_assets(_parse_asset_list(args.assets), config.risk.allowed_assets)
    calibration_adjustments = _asset_calibration_for_refresh(config)
    rows = generate_crypto_probability_rows(
        client,
        assets=assets,
        now=datetime.now(timezone.utc),
        limit=args.limit,
        pages=args.pages,
        probability_shrink=args.probability_shrink,
        market_blend=getattr(args, "market_blend", 0.15),
        max_model_market_gap=getattr(args, "max_model_market_gap", 0.35),
        min_annual_volatility=getattr(args, "min_annual_vol", 0.0),
        max_annual_volatility=getattr(args, "max_annual_vol", 1.75),
        min_edge=config.risk.min_edge_dollars,
        max_rows_per_asset=args.max_rows_per_asset,
        max_spread=config.risk.max_spread_dollars,
        min_horizon_minutes=config.risk.min_time_to_close_minutes,
        max_horizon_minutes=config.risk.max_time_to_close_minutes,
        calibration_adjustments=calibration_adjustments,
    )
    if not args.dry_run:
        write_crypto_probability_csv(args.output, rows)
    print(
        {
            "dry_run": args.dry_run,
            "output": str(args.output),
            "assets": assets,
            "calibration": {asset: adjustment.to_dict() for asset, adjustment in calibration_adjustments.items()},
            "market_blend": getattr(args, "market_blend", 0.15),
            "max_model_market_gap": getattr(args, "max_model_market_gap", 0.35),
            "volatility_regime": {
                "min_annual_vol": getattr(args, "min_annual_vol", 0.0),
                "max_annual_vol": getattr(args, "max_annual_vol", 1.75),
            },
            "rows": len(rows),
            "top": [
                {
                    "ticker": row.ticker,
                    "asset": row.symbol,
                    "side": row.best_side,
                    "edge": row.best_edge,
                    "p_yes": row.estimated_probability,
                    "strike": row.strike,
                    "spot": row.spot,
                    "horizon_min": row.horizon_minutes,
                    "yes_ask": row.yes_ask,
                    "no_ask": row.no_ask,
                }
                for row in rows[:10]
            ],
        }
    )
    return 0


def _asset_calibration_for_refresh(config):
    if not config.risk.calibration_enabled:
        return {}
    return load_asset_calibration(
        config.db_path,
        mode="live",
        min_samples=config.risk.calibration_min_samples,
        window_trades=config.risk.calibration_window_trades,
        strength=config.risk.calibration_strength,
        max_adjustment=config.risk.calibration_max_adjustment,
    )


def _asset_performance_guard_for_mode(config, ledger: PaperLedger, mode: TradeMode):
    if mode != TradeMode.LIVE or not config.risk.performance_guard_enabled:
        return {}
    return evaluate_asset_performance_guard(
        ledger.path,
        mode=mode.value,
        min_trades=config.risk.performance_guard_min_trades,
        window_trades=config.risk.performance_guard_window_trades,
        min_net_pnl_dollars=config.risk.performance_guard_min_net_pnl_dollars,
        min_avg_clv=config.risk.performance_guard_min_avg_clv,
    )


def _bucket_performance_guard_for_mode(config, ledger: PaperLedger, mode: TradeMode):
    if mode != TradeMode.LIVE or not config.risk.performance_guard_enabled:
        return {}
    return evaluate_bucket_performance_guard(
        ledger.path,
        mode=mode.value,
        min_trades=config.risk.performance_guard_min_trades,
        window_trades=config.risk.performance_guard_window_trades,
        min_net_pnl_dollars=config.risk.performance_guard_min_net_pnl_dollars,
        min_avg_clv=config.risk.performance_guard_min_avg_clv,
    )


def run_reconcile(args: argparse.Namespace, client: KalshiClient, ledger: PaperLedger) -> int:
    orders = ledger.list_unsettled_paper_orders(limit=args.limit)
    settled = 0
    still_open = 0
    errors = 0
    net_pnl = 0.0
    examples = []
    for order in orders:
        try:
            market = client.get_market(order["ticker"])
        except KalshiApiError as exc:
            errors += 1
            examples.append({"ticker": order["ticker"], "error": str(exc)[:160]})
            continue

        if market.status != "settled" or market.settlement_value is None:
            still_open += 1
            continue

        settlement = calculate_paper_settlement(
            outcome=order["outcome"],
            count=order["count"],
            entry_price=order["price"],
            settlement_value=market.settlement_value,
            include_fee_estimate=not args.no_fee_estimate,
        )
        if not args.dry_run:
            ledger.mark_paper_settled(
                order_id=order["id"],
                outcome_result=settlement.outcome_result,
                settlement_value=settlement.settlement_value,
                settled_at=market.settlement_ts,
                gross_pnl_dollars=settlement.gross_pnl_dollars,
                fee_estimate_dollars=settlement.fee_estimate_dollars,
                net_pnl_dollars=settlement.net_pnl_dollars,
            )
        settled += 1
        net_pnl += settlement.net_pnl_dollars
        if len(examples) < 8:
            examples.append(
                {
                    "ticker": order["ticker"],
                    "result": settlement.outcome_result,
                    "entry": order["price"],
                    "settlement_value": settlement.settlement_value,
                    "net_pnl": settlement.net_pnl_dollars,
                }
            )

    print(
        {
            "checked": len(orders),
            "settled": settled,
            "still_open": still_open,
            "errors": errors,
            "net_pnl_delta": round(net_pnl, 4),
            "dry_run": args.dry_run,
            "settlement_summary": ledger.settlement_summary(),
            "examples": examples,
        }
    )
    return 0 if errors == 0 else 1


def run_reconcile_live(args: argparse.Namespace, client: KalshiClient, ledger: PaperLedger) -> int:
    orders = ledger.list_unsettled_live_orders()
    if not orders:
        print({"live_checked": 0, "live_settled": 0, "dry_run": args.dry_run})
        return 0

    settlements, cursor = client.list_settlements(limit=args.limit)
    settlement_by_ticker = {str(settlement.get("ticker")): settlement for settlement in settlements}
    settled = 0
    still_open = 0
    examples = []
    net_pnl = 0.0
    for order in orders:
        settlement = settlement_by_ticker.get(order["ticker"])
        if not settlement:
            still_open += 1
            continue

        live_settlement = _calculate_live_settlement(order, settlement)
        if not args.dry_run:
            ledger.mark_live_settled(
                order_id=order["id"],
                outcome_result=live_settlement["outcome_result"],
                settlement_value=live_settlement["settlement_value"],
                settled_at=live_settlement["settled_at"],
                gross_pnl_dollars=live_settlement["gross_pnl_dollars"],
                fee_estimate_dollars=live_settlement["fee_estimate_dollars"],
                net_pnl_dollars=live_settlement["net_pnl_dollars"],
            )
        settled += 1
        net_pnl += live_settlement["net_pnl_dollars"]
        if len(examples) < 8:
            examples.append(
                {
                    "ticker": order["ticker"],
                    "result": live_settlement["outcome_result"],
                    "entry": order["average_fill_price"],
                    "settlement_value": live_settlement["settlement_value"],
                    "net_pnl": live_settlement["net_pnl_dollars"],
                    "settlement_fee_cost": live_settlement["settlement_fee_cost"],
                }
            )

    print(
        {
            "live_checked": len(orders),
            "live_settled": settled,
            "still_open": still_open,
            "net_pnl_delta": round(net_pnl, 4),
            "dry_run": args.dry_run,
            "cursor": cursor,
            "examples": examples,
        }
    )
    return 0


def run_sync_live_orders(client: KalshiClient, ledger: PaperLedger) -> int:
    orders = ledger.list_unsettled_live_orders()
    synced = 0
    errors = 0
    examples = []
    for order in orders:
        exchange_order_id = order.get("exchange_order_id")
        if not exchange_order_id:
            continue
        try:
            exchange_order = client.get_order(str(exchange_order_id))
        except KalshiApiError as exc:
            errors += 1
            if len(examples) < 8:
                examples.append({"local_order_id": order["id"], "error": str(exc)[:160]})
            continue

        fill_count = _optional_float(exchange_order.get("fill_count_fp")) or 0.0
        remaining_count = _optional_float(exchange_order.get("remaining_count_fp")) or 0.0
        average_fill_price = _exchange_fill_price(order["outcome"], exchange_order)
        fee_paid = (_optional_float(exchange_order.get("maker_fees_dollars")) or 0.0) + (
            _optional_float(exchange_order.get("taker_fees_dollars")) or 0.0
        )
        status = _local_status_from_exchange_order(exchange_order, fill_count, remaining_count)
        ledger.sync_live_order_from_exchange(
            order_id=order["id"],
            status=status,
            fill_count=fill_count,
            remaining_count=remaining_count,
            average_fill_price=average_fill_price,
            fee_paid=round(fee_paid, 4),
        )
        synced += 1
        if len(examples) < 8:
            examples.append(
                {
                    "local_order_id": order["id"],
                    "ticker": order["ticker"],
                    "status": status,
                    "exchange_status": exchange_order.get("status"),
                    "fill_count": fill_count,
                    "remaining_count": remaining_count,
                    "average_fill_price": average_fill_price,
                }
            )

    print({"live_orders_checked": len(orders), "live_orders_synced": synced, "errors": errors, "examples": examples})
    return 0 if errors == 0 else 1


def run_sync_live_exits(client: KalshiClient, ledger: PaperLedger) -> int:
    exits = ledger.list_live_exits_to_sync()
    synced = 0
    errors = 0
    examples = []
    for entry in exits:
        exit_order_id = entry.get("exit_order_id")
        if not exit_order_id:
            continue
        try:
            exchange_order = client.get_order(str(exit_order_id))
            fills = _all_fills_for_order(client, str(exit_order_id))
        except KalshiApiError as exc:
            errors += 1
            if len(examples) < 8:
                examples.append({"local_order_id": entry["id"], "exit_order_id": exit_order_id, "error": str(exc)[:160]})
            continue

        fill_stats = _fill_stats_for_outcome(entry["outcome"], fills)
        exchange_fill_count = _optional_float(exchange_order.get("fill_count_fp")) or 0.0
        exchange_remaining_count = _optional_float(exchange_order.get("remaining_count_fp")) or 0.0
        exit_fill_count = fill_stats["count"] if fill_stats["count"] > 0 else exchange_fill_count
        exit_remaining_count = exchange_remaining_count
        if exit_remaining_count <= 0 and entry.get("exit_count"):
            exit_remaining_count = max(float(entry["exit_count"]) - exit_fill_count, 0.0)
        exit_average_fill_price = fill_stats["average_price"]
        if exit_average_fill_price is None and exchange_fill_count > 0:
            exit_average_fill_price = entry.get("exit_average_fill_price") or entry.get("exit_price")
        exit_fee_paid = fill_stats["fee_paid"]
        if not fills:
            exit_fee_paid = (_optional_float(exchange_order.get("maker_fees_dollars")) or 0.0) + (
                _optional_float(exchange_order.get("taker_fees_dollars")) or 0.0
            )
        exit_status = _exit_status_from_response(
            float(entry.get("exit_count") or exit_fill_count + exit_remaining_count),
            {"fill_count": exit_fill_count, "remaining_count": exit_remaining_count},
        )
        ledger.sync_live_exit_from_fills(
            entry_order_id=entry["id"],
            exit_fill_count=round(exit_fill_count, 4),
            exit_remaining_count=round(exit_remaining_count, 4),
            exit_average_fill_price=exit_average_fill_price,
            exit_fee_paid=round(exit_fee_paid, 4),
            exit_status=exit_status,
            source="fills" if fills else "exchange_order",
        )
        synced += 1
        if len(examples) < 8:
            examples.append(
                {
                    "local_order_id": entry["id"],
                    "ticker": entry["ticker"],
                    "outcome": entry["outcome"],
                    "exit_status": exit_status,
                    "exit_fill_count": round(exit_fill_count, 4),
                    "exit_average_fill_price": exit_average_fill_price,
                    "exit_fee_paid": round(exit_fee_paid, 4),
                    "fills": len(fills),
                }
            )

    print({"live_exits_checked": len(exits), "live_exits_synced": synced, "errors": errors, "examples": examples})
    return 0 if errors == 0 else 1


def run_cancel_live_resting(args: argparse.Namespace, config, client: KalshiClient, ledger: PaperLedger) -> int:
    if not args.dry_run and config.kalshi.allow_live != "I_ACCEPT_KALSHI_LIVE_RISK":
        raise SystemExit("Live cancel blocked. Set KALSHI_ALLOW_LIVE=I_ACCEPT_KALSHI_LIVE_RISK to opt in.")

    orders = ledger.list_unsettled_live_orders()
    checked = 0
    canceled = 0
    skipped = 0
    synced = 0
    errors = 0
    examples = []
    closed_statuses = {"canceled", "cancelled", "expired", "executed"}
    for order in orders:
        exchange_order_id = order.get("exchange_order_id")
        if not exchange_order_id:
            skipped += 1
            continue
        checked += 1
        try:
            exchange_order = client.get_order(str(exchange_order_id))
        except KalshiApiError as exc:
            errors += 1
            if len(examples) < 8:
                examples.append({"local_order_id": order["id"], "error": str(exc)[:160]})
            continue

        fill_count = _optional_float(exchange_order.get("fill_count_fp")) or 0.0
        remaining_count = _optional_float(exchange_order.get("remaining_count_fp")) or 0.0
        exchange_status = str(exchange_order.get("status", "")).lower()
        if remaining_count <= 0 or exchange_status in closed_statuses:
            if args.dry_run:
                skipped += 1
                if len(examples) < 8:
                    examples.append(
                        {
                            "local_order_id": order["id"],
                            "ticker": order["ticker"],
                            "would_sync_status": _local_status_from_exchange_order(
                                exchange_order,
                                fill_count,
                                remaining_count,
                            ),
                        }
                    )
                continue
            status = _local_status_from_exchange_order(exchange_order, fill_count, remaining_count)
            ledger.sync_live_order_from_exchange(
                order_id=order["id"],
                status=status,
                fill_count=fill_count,
                remaining_count=remaining_count,
                average_fill_price=_exchange_fill_price(order["outcome"], exchange_order),
                fee_paid=round(
                    (_optional_float(exchange_order.get("maker_fees_dollars")) or 0.0)
                    + (_optional_float(exchange_order.get("taker_fees_dollars")) or 0.0),
                    4,
                ),
            )
            synced += 1
            skipped += 1
            continue

        if args.dry_run:
            canceled += 1
            if len(examples) < 8:
                examples.append(
                    {
                        "local_order_id": order["id"],
                        "ticker": order["ticker"],
                        "would_cancel_remaining": remaining_count,
                    }
                )
            continue

        try:
            response = client.cancel_event_order_v2(str(exchange_order_id))
        except KalshiApiError as exc:
            errors += 1
            if len(examples) < 8:
                examples.append({"local_order_id": order["id"], "ticker": order["ticker"], "error": str(exc)[:160]})
            continue

        canceled += 1
        try:
            exchange_order = client.get_order(str(exchange_order_id))
            fill_count = _optional_float(exchange_order.get("fill_count_fp")) or fill_count
            remaining_count = _optional_float(exchange_order.get("remaining_count_fp")) or 0.0
            average_fill_price = _exchange_fill_price(order["outcome"], exchange_order)
            fee_paid = (_optional_float(exchange_order.get("maker_fees_dollars")) or 0.0) + (
                _optional_float(exchange_order.get("taker_fees_dollars")) or 0.0
            )
        except KalshiApiError:
            exchange_order = {"status": "canceled"}
            remaining_count = 0.0
            average_fill_price = order.get("average_fill_price")
            fee_paid = 0.0
        status = _local_status_from_exchange_order(exchange_order, fill_count, remaining_count)
        ledger.sync_live_order_from_exchange(
            order_id=order["id"],
            status=status,
            fill_count=fill_count,
            remaining_count=remaining_count,
            average_fill_price=average_fill_price,
            fee_paid=round(fee_paid, 4),
        )
        synced += 1
        if len(examples) < 8:
            examples.append(
                {
                    "local_order_id": order["id"],
                    "ticker": order["ticker"],
                    "status": status,
                    "canceled_response": response,
                }
            )

    print(
        {
            "live_orders_checked": checked,
            "live_orders_canceled": canceled,
            "live_orders_synced": synced,
            "skipped": skipped,
            "errors": errors,
            "dry_run": args.dry_run,
            "examples": examples,
        }
    )
    return 0 if errors == 0 else 1


def run_live_ready(config, client: KalshiClient, ledger: PaperLedger | None = None) -> int:
    checks = {
        "environment_is_production": config.kalshi.environment == "production",
        "credentials_configured": config.kalshi.has_credentials,
        "live_opt_in": config.kalshi.allow_live == "I_ACCEPT_KALSHI_LIVE_RISK",
    }
    exchange_status = client.get_exchange_status()
    checks["exchange_active"] = bool(exchange_status.get("exchange_active"))
    checks["trading_active"] = bool(exchange_status.get("trading_active"))

    balance = None
    available_balance_dollars = 0.0
    if checks["credentials_configured"]:
        try:
            balance = client.get_balance()
            available_balance_dollars = _balance_dollars(balance)
            checks["balance_fetch_ok"] = True
        except KalshiApiError as exc:
            checks["balance_fetch_ok"] = False
            balance = {"error": str(exc)}
    else:
        checks["balance_fetch_ok"] = False
    adaptive_report = _adaptive_risk_for_mode(config, ledger, TradeMode.LIVE) if ledger is not None else None
    effective_max_position = (
        adaptive_report.effective_max_position_dollars if adaptive_report is not None else config.risk.max_position_dollars
    )
    checks["balance_covers_max_position"] = available_balance_dollars >= effective_max_position

    ready = all(checks.values())
    print(
        {
            "ready_for_live_orders": ready,
            "checks": checks,
            "risk_defaults": {
                "bankroll_dollars": config.risk.bankroll_dollars,
                "max_position_dollars": config.risk.max_position_dollars,
                "max_open_risk_dollars": config.risk.max_open_risk_dollars,
                "daily_loss_limit_dollars": config.risk.daily_loss_limit_dollars,
                "min_edge_dollars": config.risk.min_edge_dollars,
                "allowed_assets": config.risk.allowed_assets,
                "max_spread_dollars": config.risk.max_spread_dollars,
                "min_time_to_close_minutes": config.risk.min_time_to_close_minutes,
                "max_time_to_close_minutes": config.risk.max_time_to_close_minutes,
                "max_bankroll_fraction_per_trade": config.risk.max_bankroll_fraction_per_trade,
                "kelly_fraction": config.risk.kelly_fraction,
                "calibration_enabled": config.risk.calibration_enabled,
                "calibration_min_samples": config.risk.calibration_min_samples,
                "calibration_window_trades": config.risk.calibration_window_trades,
                "calibration_strength": config.risk.calibration_strength,
                "calibration_max_adjustment": config.risk.calibration_max_adjustment,
                "performance_guard_enabled": config.risk.performance_guard_enabled,
                "performance_guard_min_trades": config.risk.performance_guard_min_trades,
                "performance_guard_window_trades": config.risk.performance_guard_window_trades,
                "performance_guard_min_net_pnl_dollars": config.risk.performance_guard_min_net_pnl_dollars,
                "performance_guard_min_avg_clv": config.risk.performance_guard_min_avg_clv,
            },
            "adaptive_risk": adaptive_report.to_dict() if adaptive_report is not None else None,
            "balance": balance,
            "available_balance_dollars": available_balance_dollars,
            "live_command_shape": (
                "kalshi-bot scan --strategy probability-file "
                "--probability-file data/probabilities.csv --live"
            ),
        }
    )
    return 0 if ready else 1


def run_take_profit(args: argparse.Namespace, config, client: KalshiClient, ledger: PaperLedger) -> int:
    if args.execute and config.kalshi.allow_live != "I_ACCEPT_KALSHI_LIVE_RISK":
        raise SystemExit("Live take-profit execution blocked. Set KALSHI_ALLOW_LIVE=I_ACCEPT_KALSHI_LIVE_RISK.")

    entries = ledger.list_live_entries_without_exit()
    actions = []
    submitted = 0
    for entry in entries:
        outcome = OutcomeSide(entry["outcome"])
        entry_price = entry["average_fill_price"]
        target_price = _target_exit_price(
            entry_price=entry_price,
            profit_pct=args.profit_pct,
            min_profit_cents=args.min_profit_cents,
        )
        order = _exit_order_from_entry(client, entry, outcome, target_price)
        action = {
            "entry_order_id": entry["id"],
            "ticker": entry["ticker"],
            "outcome": outcome.value,
            "entry_price": round(entry_price, 4),
            "target_price": round(target_price, 4),
            "exit_ready": order is not None,
            "execute": args.execute,
        }
        if order is not None:
            expected_profit = round((order.price - entry_price) * order.count, 4)
            action.update(
                {
                    "exit_limit_price": round(order.price, 4),
                    "count": order.count,
                    "expected_profit_if_filled": expected_profit,
                }
            )
        if args.execute and order is not None:
            response = client.create_event_order_v2(order)
            exit_fill_count = _optional_float(response.get("fill_count")) or 0.0
            exit_remaining_count = _optional_float(response.get("remaining_count")) or 0.0
            exit_average_fill_price = _event_order_response_exit_price(order, response) or order.price
            exit_fee_paid = _event_order_response_fee_paid(response, exit_fill_count)
            exit_status = _exit_status_from_response(order.count, response)
            ledger.mark_exit_submitted(
                entry_order_id=entry["id"],
                exit_order_id=response["order_id"],
                exit_client_order_id=order.client_order_id,
                exit_price=order.price,
                exit_count=order.count,
                exit_fill_count=exit_fill_count,
                exit_remaining_count=exit_remaining_count,
                take_profit_threshold=target_price,
                status=exit_status,
                exit_average_fill_price=exit_average_fill_price,
                exit_fee_paid=exit_fee_paid,
            )
            action["exit_status"] = exit_status
            action["exit_average_fill_price"] = exit_average_fill_price
            action["exit_fee_paid"] = exit_fee_paid
            action["exchange_response"] = response
            submitted += 1
        actions.append(action)

    print(
        {
            "execute": args.execute,
            "profit_pct": args.profit_pct,
            "min_profit_cents": args.min_profit_cents,
            "entries_checked": len(entries),
            "exit_orders_submitted": submitted,
            "actions": actions,
        }
    )
    return 0


def run_optimize(args: argparse.Namespace, config, ledger: PaperLedger) -> int:
    samples = ledger.load_signal_samples()
    if not samples:
        print({"error": "no logged signals yet; run kalshi-bot scan first"})
        return 1

    baseline_params = SimulationParams(
        bankroll_dollars=config.risk.bankroll_dollars,
        min_edge_dollars=config.risk.min_edge_dollars,
        max_position_dollars=config.risk.max_position_dollars,
        max_open_risk_dollars=config.risk.max_open_risk_dollars,
        probability_haircut=0.80,
        exclude_multivariate=not args.include_multivariate,
    )
    baseline = simulate(samples, baseline_params, mc_trials=args.mc_trials, seed=args.seed)
    results = random_search(
        samples,
        search_trials=args.search_trials,
        mc_trials=args.mc_trials,
        seed=args.seed,
        top_n=args.top,
        allow_multivariate=args.include_multivariate,
    )

    print("baseline")
    print(_result_row(baseline))
    print("top_results")
    for index, result in enumerate(results, start=1):
        print({"rank": index, **_result_row(result)})
    print(
        {
            "signals_loaded": len(samples),
            "warning": "Monte Carlo uses logged estimated probabilities, not settled outcomes. Treat as optimizer plumbing until settlement data exists.",
        }
    )
    return 0


def _scan_args_for_loop(args: argparse.Namespace, *, trading_active: bool = True) -> argparse.Namespace:
    return argparse.Namespace(
        strategy="probability-file",
        probability_file=args.probability_file,
        limit=args.limit,
        pages=args.pages,
        skip_pages=args.skip_pages,
        min_volume=0.0,
        workers=1,
        include_multivariate=args.include_multivariate,
        live=True,
        demo=False,
        dry_run=not (args.enable_live_buys and trading_active),
    )


def _adaptive_risk_for_mode(config, ledger: PaperLedger, mode: TradeMode):
    window = max(config.risk.adaptive_window_trades, config.risk.adaptive_min_settled_trades)
    rows = ledger.recent_realized_orders(mode, limit=window)
    return evaluate_adaptive_risk(config.risk, rows)


def _take_profit_args_for_loop(args: argparse.Namespace, *, trading_active: bool = True) -> argparse.Namespace:
    return argparse.Namespace(
        profit_pct=args.profit_pct,
        min_profit_cents=args.min_profit_cents,
        execute=args.execute_exits and trading_active,
    )


def _reconcile_live_args_for_loop() -> argparse.Namespace:
    return argparse.Namespace(limit=100, dry_run=False)


def _refresh_btc_args_for_loop(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        output=args.probability_file,
        series=args.btc_series,
        limit=args.limit,
        pages=args.pages,
        annual_vol=args.btc_annual_vol,
        probability_shrink=args.btc_probability_shrink,
        market_blend=getattr(args, "btc_market_blend", 0.15),
        max_model_market_gap=getattr(args, "btc_max_model_market_gap", 0.35),
        max_rows=args.btc_max_rows,
        spot=None,
        dry_run=False,
    )


def _refresh_crypto_args_for_loop(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        output=args.probability_file,
        assets=args.crypto_assets,
        limit=args.limit,
        pages=args.pages,
        probability_shrink=args.crypto_probability_shrink,
        market_blend=args.crypto_market_blend,
        max_model_market_gap=args.crypto_max_model_market_gap,
        min_annual_vol=args.crypto_min_annual_vol,
        max_annual_vol=args.crypto_max_annual_vol,
        max_rows_per_asset=args.crypto_max_rows_per_asset,
        dry_run=False,
    )


def _parse_asset_list(raw_assets: str) -> list[str]:
    assets = [asset.strip().upper() for asset in raw_assets.split(",") if asset.strip()]
    if not assets:
        raise SystemExit("At least one crypto asset is required.")
    return assets


def _allowed_refresh_assets(assets: list[str], allowed_assets: tuple[str, ...]) -> list[str]:
    if not allowed_assets:
        return assets
    allowed = set(allowed_assets)
    filtered = [asset for asset in assets if asset in allowed]
    if not filtered:
        raise SystemExit("No requested crypto assets are allowed by BOT_ALLOWED_ASSETS.")
    return filtered


def _calculate_live_settlement(order: dict, settlement: dict) -> dict:
    settlement_value = _settlement_value(settlement)
    entry_price = float(order["average_fill_price"])
    count = float(order["fill_count"])
    base = calculate_paper_settlement(
        outcome=order["outcome"],
        count=count,
        entry_price=entry_price,
        settlement_value=settlement_value,
        include_fee_estimate=False,
    )
    fee_paid = float(order.get("average_fee_paid") or 0.0)
    return {
        "outcome_result": base.outcome_result,
        "settlement_value": base.settlement_value,
        "settled_at": settlement.get("settled_time"),
        "gross_pnl_dollars": base.gross_pnl_dollars,
        "fee_estimate_dollars": round(fee_paid, 4),
        "net_pnl_dollars": round(base.gross_pnl_dollars - fee_paid, 4),
        "settlement_fee_cost": _optional_float(settlement.get("fee_cost")) or 0.0,
    }


def _settlement_value(settlement: dict) -> float:
    raw_value = _optional_float(settlement.get("value"))
    if raw_value is not None:
        return round(raw_value / 100.0 if raw_value > 1 else raw_value, 4)
    market_result = str(settlement.get("market_result", "")).lower()
    if market_result == "yes":
        return 1.0
    if market_result == "no":
        return 0.0
    return 0.0


def _fetch_markets(
    client: KalshiClient,
    *,
    limit: int,
    pages: int,
    skip_pages: int = 0,
    exclude_multivariate: bool = True,
):
    markets = []
    cursor = None
    mve_filter = "exclude" if exclude_multivariate else None
    for _ in range(skip_pages):
        _, cursor = client.list_markets(limit=limit, cursor=cursor, mve_filter=mve_filter)
        if not cursor:
            return markets
    for _ in range(pages):
        page, cursor = client.list_markets(limit=limit, cursor=cursor, mve_filter=mve_filter)
        markets.extend(page)
        if not cursor:
            break
    return markets


def _resolve_mode(args: argparse.Namespace, environment: str) -> TradeMode:
    if args.live and args.demo:
        raise SystemExit("Choose only one of --live or --demo")
    if args.live:
        if environment != "production":
            raise SystemExit("--live requires KALSHI_ENV=production")
        config = load_config()
        if not getattr(args, "dry_run", False) and config.kalshi.allow_live != "I_ACCEPT_KALSHI_LIVE_RISK":
            raise SystemExit("Live trading blocked. Set KALSHI_ALLOW_LIVE=I_ACCEPT_KALSHI_LIVE_RISK to opt in.")
        return TradeMode.LIVE
    if args.demo:
        if environment != "demo":
            raise SystemExit("--demo requires KALSHI_ENV=demo and demo credentials")
        return TradeMode.DEMO
    return TradeMode.PAPER


def _order_status_from_response(mode: TradeMode, response: dict) -> str:
    fill_count = _optional_float(response.get("fill_count")) or 0.0
    if fill_count > 0:
        return f"{mode.value}_executed"
    return f"{mode.value}_submitted"


def _exit_status_from_response(order_count: float, response: dict) -> str:
    fill_count = _optional_float(response.get("fill_count")) or 0.0
    remaining_count = _optional_float(response.get("remaining_count")) or max(order_count - fill_count, 0.0)
    if fill_count >= order_count - 0.0001 and remaining_count <= 0.0001:
        return "exit_executed"
    if fill_count > 0:
        return "exit_partial"
    return "exit_submitted"


def _local_status_from_exchange_order(exchange_order: dict, fill_count: float, remaining_count: float) -> str:
    if fill_count > 0:
        return "live_executed"
    exchange_status = str(exchange_order.get("status", "")).lower()
    if remaining_count <= 0 or exchange_status in {"canceled", "cancelled", "expired", "executed"}:
        return "live_closed"
    return "live_submitted"


def _exchange_fill_price(outcome: str, exchange_order: dict) -> float | None:
    if OutcomeSide(outcome) == OutcomeSide.YES:
        return _optional_float(exchange_order.get("yes_price_dollars"))
    return _optional_float(exchange_order.get("no_price_dollars"))


def _event_order_response_exit_price(order: ProposedOrder, response: dict) -> float | None:
    if order.outcome == OutcomeSide.YES:
        return _optional_float(response.get("yes_price_dollars")) or _optional_float(response.get("average_fill_price"))
    outcome_price = _optional_float(response.get("no_price_dollars"))
    if outcome_price is not None:
        return outcome_price
    average_fill_price = _optional_float(response.get("average_fill_price"))
    if average_fill_price is None:
        return None
    return round(1.0 - average_fill_price, 4)


def _event_order_response_fee_paid(response: dict, fill_count: float) -> float:
    maker_fee = _optional_float(response.get("maker_fees_dollars"))
    taker_fee = _optional_float(response.get("taker_fees_dollars"))
    if maker_fee is not None or taker_fee is not None:
        return round((maker_fee or 0.0) + (taker_fee or 0.0), 4)
    average_fee_paid = _optional_float(response.get("average_fee_paid")) or 0.0
    return round(average_fee_paid * max(fill_count, 0.0), 4)


def _all_fills_for_order(client: KalshiClient, order_id: str) -> list[dict[str, Any]]:
    fills: list[dict[str, Any]] = []
    cursor = None
    while True:
        page, cursor = client.list_fills(order_id=order_id, limit=1000, cursor=cursor)
        fills.extend(page)
        if not cursor:
            return fills


def _fill_stats_for_outcome(outcome: str, fills: list[dict[str, Any]]) -> dict[str, float | None]:
    side = OutcomeSide(outcome)
    count = 0.0
    weighted_price = 0.0
    fee_paid = 0.0
    for fill in fills:
        fill_count = _optional_float(fill.get("count_fp")) or _optional_float(fill.get("count")) or 0.0
        if fill_count <= 0:
            continue
        price = (
            _optional_float(fill.get("yes_price_dollars"))
            if side == OutcomeSide.YES
            else _optional_float(fill.get("no_price_dollars"))
        )
        if price is None:
            continue
        count += fill_count
        weighted_price += fill_count * price
        fee_paid += _optional_float(fill.get("fee_cost")) or 0.0
    average_price = round(weighted_price / count, 4) if count > 0 else None
    return {"count": round(count, 4), "average_price": average_price, "fee_paid": round(fee_paid, 4)}


def _order_from_signal(client: KalshiClient, signal, count: float) -> ProposedOrder:
    # Kalshi V2 quotes the YES book: bid buys YES; ask sells YES, economically a NO buy.
    book_side = BookSide.BID if signal.outcome == OutcomeSide.YES else BookSide.ASK
    price = _post_only_price(client, signal.ticker, signal.outcome, signal.reference_price)
    return ProposedOrder(
        ticker=signal.ticker,
        book_side=book_side,
        outcome=signal.outcome,
        count=count,
        price=price,
        client_order_id=client.make_client_order_id(),
        post_only=True,
    )


def _exit_order_from_entry(
    client: KalshiClient,
    entry: dict,
    outcome: OutcomeSide,
    target_price: float,
) -> ProposedOrder | None:
    top = client.get_orderbook(entry["ticker"])
    if outcome == OutcomeSide.YES:
        if top.yes_bid is None or top.yes_bid < target_price:
            return None
        price = target_price
        book_side = BookSide.ASK
    else:
        if top.no_bid is None or top.no_bid < target_price:
            return None
        price = target_price
        book_side = BookSide.BID

    return ProposedOrder(
        ticker=entry["ticker"],
        book_side=book_side,
        outcome=outcome,
        count=entry["fill_count"],
        price=round(min(max(price, 0.01), 0.99), 4),
        client_order_id=client.make_client_order_id("tp"),
        post_only=False,
        reduce_only=True,
        time_in_force="immediate_or_cancel",
    )


def _target_exit_price(*, entry_price: float, profit_pct: float, min_profit_cents: float) -> float:
    pct_target = entry_price * (1.0 + profit_pct / 100.0)
    cents_target = entry_price + (min_profit_cents / 100.0)
    return round(min(max(pct_target, cents_target), 0.99), 4)


def _post_only_price(client: KalshiClient, ticker: str, outcome: OutcomeSide, fallback_price: float) -> float:
    top = client.get_orderbook(ticker)
    tick = 0.01
    if outcome == OutcomeSide.YES and top.yes_bid is not None and top.yes_ask is not None:
        if top.yes_ask - top.yes_bid > tick:
            return round(min(top.yes_bid + tick, top.yes_ask - tick), 4)
        return round(top.yes_bid, 4)
    if outcome == OutcomeSide.NO and top.no_bid is not None and top.no_ask is not None:
        if top.no_ask - top.no_bid > tick:
            return round(min(top.no_bid + tick, top.no_ask - tick), 4)
        return round(top.no_bid, 4)
    return fallback_price


def _result_row(result) -> dict:
    params = result.params
    return {
        "selected_signals": result.selected_signals,
        "avg_trades": round(result.avg_trades, 2),
        "mean_pnl": round(result.mean_pnl, 4),
        "median_pnl": round(result.median_pnl, 4),
        "cvar_5_pnl": round(result.cvar_5_pnl, 4),
        "loss": result.loss,
        "min_edge": params.min_edge_dollars,
        "max_position": params.max_position_dollars,
        "max_open_risk": params.max_open_risk_dollars,
        "max_contracts": params.max_contracts,
        "probability_haircut": params.probability_haircut,
        "exclude_multivariate": params.exclude_multivariate,
    }


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KalshiApiError as exc:
        print(f"Kalshi API error: {exc}", file=sys.stderr)
        raise SystemExit(1)


def _balance_dollars(balance: dict | None) -> float:
    if not balance:
        return 0.0
    value = balance.get("balance_dollars")
    if value is not None:
        return float(value)
    raw_balance = balance.get("balance")
    if raw_balance is not None:
        return float(raw_balance) / 100
    return 0.0


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
