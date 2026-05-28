# Learning Loop Design

The bot should improve like an ML system, but the core target is not "price went up after entry." The target is whether our estimated probability was better than the executable Kalshi price after fees, slippage, and settlement.

## Data

Each candidate needs these fields:

- Market ticker, category, title, close time, status.
- Orderbook top and spread at decision time.
- Our estimated probability for the selected outcome.
- Executable price and expected fees.
- Decision: skipped, paper order, demo order, live order.
- Result: settled win/loss, realized P&L, max adverse movement if available.

No data, no learning. The current ledger is only a start.

## Model

Start with simple calibrated models before complex ML:

1. Market filters: remove bad market classes, low liquidity, too-wide spreads, bad categories.
2. Probability model: estimate outcome probability from external data.
3. Calibration layer: shrink overconfident probabilities toward market price.
4. Sizing layer: convert edge into position size under bankroll constraints.
5. Execution layer: post-only or limit order rules.

## Loss Function

The optimizer now uses a Monte Carlo loss:

```text
loss = -mean_pnl + 1.5 * max(0, -cvar_5_pnl) + churn_penalty + inactivity_penalty
```

That means it rewards expected profit, penalizes bad left-tail outcomes, and lightly penalizes excessive trading. Once settlement data exists, the stronger training objective should combine:

- Brier score for probability calibration.
- Negative realized after-fee P&L.
- Drawdown / CVaR penalty.
- Overtrading penalty.
- Category concentration penalty.

## Validation

Do not train and test on the same markets. Use time splits:

- Train on older settled paper trades.
- Tune parameters on a validation window.
- Report only the newest untouched test window.

This prevents the optimizer from memorizing quirks in a tiny dataset.

## Current Result

With only three logged signals, the optimizer is not statistically meaningful yet. A safe run excluding `KXMVE` markets selected zero trades. An unsafe diagnostic run including `KXMVE` markets found attractive simulated P&L, but that is exactly the kind of low-liquidity artifact we want the system to learn to avoid.

Update: the bot now has a `reconcile` command that checks public market settlement fields and marks local paper orders as settled once Kalshi reports a settlement value. The next improvement is to feed settled paper P&L into the optimizer directly instead of using simulated Bernoulli outcomes.

## Next Engineering Steps

1. Expand the signal schema with market volume, category, close time, bid/ask sizes, and spread.
2. Feed settled paper P&L into the optimizer instead of simulated outcomes.
3. Add a backtest command that uses real settled outcomes instead of simulated Bernoulli draws.
4. Add train/validation/test splits by signal timestamp.
5. Add domain-specific probability models, one category at a time.
6. Promote only models that beat the market after fees on out-of-sample settled paper trades.
