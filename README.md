# Kalshi Iterative Bot

This folder contains a conservative starter bot for Kalshi. It is built to learn first, trade later:

- Public market-data scans work without credentials.
- Paper mode is the default.
- Demo/live order submission is behind explicit command and environment gates.
- Risk defaults assume a $20 tranche: $1 max position, $5 max open risk, $2 daily loss stop, and an 8 cent minimum modeled edge.

This is not financial advice and does not guarantee profit. Prediction markets are competitive, fees matter, and small accounts can be consumed quickly by bad fills.

## Current State

Implemented:

- REST client for public market data, exchange health, authenticated balance, and V2 event orders.
- SQLite paper ledger at `data/paper_trades.sqlite3`.
- Risk manager with edge, position-size, open-risk, and daily-loss checks.
- Capped fractional-Kelly sizing: `min(max_position, bankroll_fraction, fractional_kelly, remaining_open_risk)`.
- `spread-maker` paper-only baseline for collecting data on wide-spread markets.
- `probability-file` strategy for your own independent probability estimates.
- Paper settlement reconciliation.
- Reduce-only live take-profit exits.
- Continuous loop command for unattended checking.
- Read-only localhost dashboard with P&L, returns, and calibration metrics.
- Adaptive risk multiplier that scales sizing up/down from settled trade evidence.
- Unit tests for risk sizing and orderbook price complements.

Not implemented yet:

- Backtesting/replay engine.
- Domain-specific forecast models.
- WebSocket orderbook/fill streaming.

## Setup

```bash
cd /Users/nathan/Documents/Kalshi
source .venv/bin/activate
kalshi-bot health
```

If the virtual environment is missing:

```bash
cd /Users/nathan/Documents/Kalshi
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## Run A Paper Scan

```bash
cd /Users/nathan/Documents/Kalshi
.venv/bin/kalshi-bot scan --limit 100 --pages 1
.venv/bin/kalshi-bot status
```

The default strategy is paper-only. It proposes tiny inside-spread maker quotes on markets with wide spreads. Treat it as a data collector, not a live strategy. It uses Kalshi's `mve_filter=exclude` market filter by default because early scans showed multivariate/parlay markets can create fake-looking spread edge on thin books.

To keep collecting later filtered pages without duplicating the same first pages:

```bash
.venv/bin/kalshi-bot scan --limit 100 --skip-pages 60 --pages 40 --min-volume 0
```

## Run The Optimizer

The optimizer runs Monte Carlo simulations over logged paper signals and randomizes risk parameters such as minimum edge, max position size, max open risk, and probability haircut:

```bash
.venv/bin/kalshi-bot optimize --search-trials 1000 --mc-trials 1000 --top 5
```

Current limitation: until we add settled outcomes, the optimizer simulates outcomes from logged estimated probabilities. That tests the plumbing and risk tradeoffs, but it is not a proof of edge. Use `--include-multivariate` only as a diagnostic; it will currently chase low-liquidity artifacts.

## Run Probability-File Strategy

Create `data/probabilities.csv` with:

```csv
ticker,estimated_probability,notes
REAL-TICKER-HERE,0.62,Your model says 62%.
```

Then:

```bash
.venv/bin/kalshi-bot scan --strategy probability-file --probability-file data/probabilities.csv
```

The bot fetches each ticker's orderbook, derives YES/NO asks from the binary book, and records a signal only when your probability estimate clears the configured edge threshold.

## Refresh BTC Probabilities

The BTC helper writes fresh daily BTC threshold rows into `data/probabilities.csv`. It uses current BTC-USD spot, open Kalshi `KXBTCD` markets, each market's close time, and a simple lognormal volatility model:

```bash
.venv/bin/kalshi-bot refresh-btc --dry-run
.venv/bin/kalshi-bot refresh-btc
```

The notes include `close_time` and `horizon_min`, so betting cutoff and settlement target time are visible before a trade is considered. This model is intentionally simple; use it as an input that must survive live/paper reconciliation, not as proof of edge.

## Refresh Multi-Crypto Probabilities

The multi-crypto helper is additive and separate from the BTC helper. By default it writes to `data/crypto_probabilities.csv`, not the live `data/probabilities.csv` file:

```bash
.venv/bin/kalshi-bot refresh-crypto --dry-run
.venv/bin/kalshi-bot refresh-crypto
```

It currently supports BTC, ETH, SOL, XRP, and DOGE daily threshold markets when Kalshi lists them. Inputs are fact-based: Coinbase spot, Coinbase hourly candles for realized volatility, Kalshi close time, Kalshi top-of-book prices, and a conservative probability shrink. Keep this in dry-run until the BTC-only loop is stable enough to restart intentionally with `--refresh-crypto`.

## Demo And Live Gates

Demo order submission requires:

```bash
KALSHI_ENV=demo
KALSHI_API_KEY_ID=...
KALSHI_PRIVATE_KEY_PATH=...
.venv/bin/kalshi-bot scan --strategy probability-file --demo
```

Live production order submission requires both `--live` and this exact opt-in:

```bash
KALSHI_ALLOW_LIVE=I_ACCEPT_KALSHI_LIVE_RISK
```

Check readiness before placing any live order:

```bash
.venv/bin/kalshi-bot live-ready
```

Live order submission only works through the `probability-file` strategy:

```bash
.venv/bin/kalshi-bot scan --strategy probability-file --probability-file data/probabilities.csv --live
```

Keep live trading off until paper results show a repeatable edge after fees and slippage. For first live use, keep `BOT_MAX_POSITION_DOLLARS=1.00` and use only one or two hand-checked probability-file rows.

## Settlement Reconciliation

Paper settlement reconciliation fetches public market settlement fields and updates local paper orders when the market is settled:

```bash
.venv/bin/kalshi-bot reconcile
.venv/bin/kalshi-bot status
```

Authenticated account settlement history is available after credentials are configured:

```bash
.venv/bin/kalshi-bot portfolio-settlements --limit 20
```

Local live orders can be marked settled from authenticated settlement history:

```bash
.venv/bin/kalshi-bot reconcile-live
```

## Take Profit Exits

The bot can place reduce-only take-profit limit orders for filled live entries. It is dry-run by default:

```bash
.venv/bin/kalshi-bot take-profit --profit-pct 100
```

To actually submit the exit order, live opt-in must be enabled and you must pass `--execute`:

```bash
.venv/bin/kalshi-bot take-profit --profit-pct 100 --execute
```

For a YES entry at `0.06`, `--profit-pct 100` targets an exit at `0.12`. `--min-profit-cents 5` can enforce an absolute 5-cent-per-contract minimum gain.

## Continuous Server Loop

The loop command repeatedly reconciles settled live entries, checks open live entries for take-profit exits, and checks `data/probabilities.csv` for new probability-file buy signals. It is dry-run by default for buys and exits:

```bash
cd /Users/nathan/Documents/Kalshi
.venv/bin/kalshi-bot loop --interval-seconds 60 --refresh-btc
```

Test a single cycle without placing live orders:

```bash
.venv/bin/kalshi-bot loop --iterations 1 --interval-seconds 1 --profit-pct 100 --refresh-btc
```

Live buying and live exit submission require explicit flags plus the live opt-in:

```bash
export KALSHI_ALLOW_LIVE=I_ACCEPT_KALSHI_LIVE_RISK
.venv/bin/kalshi-bot loop \
  --interval-seconds 60 \
  --refresh-btc \
  --enable-live-buys \
  --execute-exits \
  --profit-pct 100
```

The loop uses these safety rules:

- `--enable-live-buys` is required before it can place new live buy orders.
- `--execute-exits` is required before it can submit live reduce-only exits.
- Existing live exposure on a ticker blocks another live buy on that ticker.
- Live submitted and live filled entries count toward open risk.
- Settled live entries are removed from local open risk through `reconcile-live`.
- `BOT_MAX_POSITION_DOLLARS` and `BOT_MAX_OPEN_RISK_DOLLARS` still apply.
- `BOT_ALLOWED_ASSETS`, `BOT_MAX_SPREAD_DOLLARS`, `BOT_MIN_TIME_TO_CLOSE_MINUTES`, and `BOT_MAX_TIME_TO_CLOSE_MINUTES` reject candidates before order sizing. Crypto refresh also skips assets outside `BOT_ALLOWED_ASSETS`.
- Adaptive risk stays at `1.0x` until enough final results exist, moves up only when PnL, CLV proxy, Brier/log loss, and drawdown checks pass, and moves down when calibration or drawdown fails.

Current tightened crypto defaults favor cleaner fills:

```env
BOT_ALLOWED_ASSETS=BTC,ETH,SOL
BOT_MAX_SPREAD_DOLLARS=0.02
BOT_MIN_TIME_TO_CLOSE_MINUTES=10
BOT_MAX_TIME_TO_CLOSE_MINUTES=60
```

## Adaptive Risk Scaling

The live scanner computes a rolling adaptive multiplier from recent realized trades before sizing new entries. It uses the last `BOT_ADAPTIVE_WINDOW_TRADES` realized orders, requires at least `BOT_ADAPTIVE_MIN_SETTLED_TRADES` final settled results before scaling up, and applies the multiplier to max position, max open risk, daily loss limit, bankroll-fraction sizing, and fractional Kelly sizing.

The CLV field is currently a proxy: final settled trades use terminal contract value minus entry price, while early take-profit exits use exit price minus entry price. That is useful for sizing discipline, but it is not a true pre-close market-price snapshot.

For a simple server session, use `tmux`:

```bash
tmux new -s kalshi-bot
cd /path/to/Kalshi
source .venv/bin/activate
kalshi-bot loop --interval-seconds 60
```

Detach with `Ctrl-b` then `d`, reattach with `tmux attach -t kalshi-bot`, and stop with `Ctrl-C` inside the session.

On a Linux server, run it as a `systemd` service once the dry-run behavior looks correct. Keep the private key path in the server's `.env`, never in git, and start live mode only after `kalshi-bot live-ready` passes.

## Recommended Build Path

1. Collect data with paper scans for at least 50 to 100 signals.
2. Use settlement reconciliation so paper P&L is real, not theoretical.
3. Build one domain model at a time, starting with markets where external data is clean.
4. Compare model probability vs Kalshi executable price after fees.
5. Use quarter-Kelly sizing only after the model is calibrated; until then keep the hard $1 cap.
6. Move from paper to demo, then to live with one $20 tranche only.

The first real edge should come from independent probability models, not from LLM confidence alone.
