# Kalshi Bot Current State

Generated for handoff into a fresh chat.

## Runtime Defaults

- Live strategy: multi-crypto probability file with `kalshi-bot loop --refresh-crypto`.
- Assets: `BTC,ETH,SOL`.
- Hard caps: `BOT_MAX_POSITION_DOLLARS=4.00`, `BOT_MAX_OPEN_RISK_DOLLARS=5.00`, `BOT_DAILY_LOSS_LIMIT_DOLLARS=2.00`.
- Bankroll fraction cap: `BOT_MAX_BANKROLL_FRACTION_PER_TRADE=0.20`, so a `$20` bankroll can actually reach the `$4` max-position cap.
- Entry threshold: `BOT_MIN_EDGE_DOLLARS=0.08`.
- Spread and time filters: max spread `0.02`, time-to-close `10` to `60` minutes.
- Adaptive risk: disabled by default.
- Historical calibration: disabled by default.
- Performance guard: disabled by default.
- Orderflow adjustment: enabled, capped at `0.04` probability points.
- Edge formula: `adjusted_probability - executable_price - fee_haircut - slippage_penalty`.

## Live Commands

Check readiness:

```bash
cd /Users/nathan/Documents/Kalshi
.venv/bin/kalshi-bot live-ready
```

Run one dry live-shaped loop:

```bash
cd /Users/nathan/Documents/Kalshi
.venv/bin/kalshi-bot loop --iterations 1 --interval-seconds 1 --refresh-crypto --profit-pct 100
```

Run live with buys and take-profit exits:

```bash
cd /Users/nathan/Documents/Kalshi
KALSHI_ALLOW_LIVE=I_ACCEPT_KALSHI_LIVE_RISK .venv/bin/kalshi-bot loop --interval-seconds 60 --refresh-crypto --enable-live-buys --execute-exits --profit-pct 100
```

Start dashboard:

```bash
cd /Users/nathan/Documents/Kalshi
.venv/bin/kalshi-bot dashboard --host 127.0.0.1 --port 8765
```

## Notes

- The bot should not use chat history as a signal.
- The default live path does not use old settled results to shift probabilities.
- The optional learning modules remain in the repo because they are tested and useful for later audited experiments.
- Private keys and `.env` are gitignored and should never be committed.
