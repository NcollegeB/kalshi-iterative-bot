# Kalshi Research Notes

## What Kalshi Is

Kalshi is a US prediction-market exchange for event contracts. Its help center says Kalshi contracts are yes/no event contracts, and its regulation page says Kalshi is regulated by the Commodity Futures Trading Commission as a Designated Contract Market.

Useful links:

- Kalshi overview: https://help.kalshi.com/en/articles/13823763-what-is-kalshi
- Regulation FAQ: https://help.kalshi.com/en/articles/13823765-how-is-kalshi-regulated
- API docs: https://docs.kalshi.com/welcome

## API Facts That Matter

- Current REST production base URL: `https://external-api.kalshi.com/trade-api/v2`
- Current REST demo base URL: `https://external-api.demo.kalshi.co/trade-api/v2`
- Current WebSocket production URL: `wss://external-api-ws.kalshi.com/trade-api/ws/v2`
- Current WebSocket demo URL: `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2`
- Public market data does not require auth.
- Authenticated requests use `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, and RSA-PSS `KALSHI-ACCESS-SIGNATURE`.
- Kalshi's docs say the old `kalshi-python` package is deprecated; current SDK packages are `kalshi_python_sync` and `kalshi_python_async`.
- V2 event orders use `/portfolio/events/orders`, `side=bid|ask`, fixed-point dollar prices, and fixed-point contract counts.
- In V2, `bid` buys YES. `ask` sells YES, which is economically equivalent to buying NO at `1 - price`.
- Fixed-point fields are now important: price strings can have up to 4 decimals; contract-count strings can use 2 decimals.
- Rate limits are token bucket based. Basic tier is listed as 200 read tokens/sec and 100 write tokens/sec, with most requests costing 10 tokens.

Useful docs:

- Environments: https://docs.kalshi.com/getting_started/api_environments
- Market data: https://docs.kalshi.com/getting_started/quick_start_market_data
- Auth: https://docs.kalshi.com/getting_started/quick_start_authenticated_requests
- Orders V2: https://docs.kalshi.com/api-reference/orders/create-order-v2
- WebSockets: https://docs.kalshi.com/getting_started/quick_start_websockets
- Rate limits: https://docs.kalshi.com/getting_started/rate_limits
- Fixed-point migration: https://docs.kalshi.com/getting_started/fixed_point_migration
- Settlement: https://docs.kalshi.com/getting_started/market_settlement

## Open Source Projects Worth Studying

- Official starter code: https://github.com/Kalshi/kalshi-starter-code-python
  - Minimal authenticated API example. Good for checking signing behavior, not a full bot.
- ryanfrigo/kalshi-ai-trading-bot: https://github.com/ryanfrigo/kalshi-ai-trading-bot
  - Useful architecture: signed client, market ingestion, SQLite telemetry, paper trading, dashboard, risk helpers, category scoring.
- OctagonAI/kalshi-deep-trading-bot: https://github.com/OctagonAI/kalshi-deep-trading-bot
  - Useful as an LLM/research-driven reference, but it depends on paid research/model APIs and should not be treated as a turnkey edge.
- pmxt: https://github.com/pmxt-dev/pmxt
  - Unified prediction-market API for Kalshi, Polymarket, and others. Interesting later for cross-market comparison/arbitrage.
- dorkalifa/predict-bot: https://github.com/dorkalifa/predict-bot
  - Contrarian, short-expiry, paper/live architecture with risk checks and backtesting ideas.
- PyKalshi: https://github.com/arshka/pykalshi
  - Unofficial Python client with WebSocket/pandas conveniences.

## Research Papers / Benchmarks

- PredictionMarketBench: https://arxiv.org/abs/2602.00133
  - Main takeaway: backtesting prediction-market agents must model orderbooks, maker/taker behavior, fees, and settlement risk. Naive agents can lose to transaction costs.
- Prediction Arena: https://arxiv.org/abs/2604.07355
  - Main takeaway: autonomous AI trading on real prediction markets performed poorly on Kalshi in the reported benchmark. Use LLMs for research assistance, not unchecked execution.

## Best Implementation Strategy For A $20 Tranche

The best approach is not to start with a fully autonomous live bot. Start with a measured research and execution loop:

1. Data first
   - Store every scanned market, orderbook top, signal, hypothetical fill, and later settlement.
   - Without this, the bot cannot improve; it can only trade.

2. Paper trading first
   - Use real market data but local simulated orders.
   - Require enough settled examples before risking even $20.

3. Independent probabilities
   - The bot needs a probability estimate that did not come from the Kalshi price itself.
   - Good candidates: weather forecasts, macro calendars, crypto reference prices, sports/statistical models where allowed, and category-specific historical models.

4. Edge after fees
   - Trade only when estimated probability exceeds executable ask by a large margin.
   - For a $20 bankroll, a minimum edge around 8 to 10 cents is more realistic than scalping 1 to 2 cents.

5. Hard risk limits
   - Keep max position around $1 until the bot has a record.
   - Keep total open risk around $5.
   - Stop for the day after around $2 realized loss.
   - Prefer post-only maker orders while learning, but model adverse selection.

6. Iterate by category
   - Track Brier score, calibration, fill quality, realized P&L, and drawdown per category.
   - Increase size only in categories with enough settled trades and positive after-fee performance.

7. Add automation slowly
   - Phase 1: paper scan and signal ledger.
   - Phase 2: settlement reconciliation and reports.
   - Phase 3: backtester/replay.
   - Phase 4: one external-data model.
   - Phase 5: demo orders.
   - Phase 6: live orders with $20 max bankroll.

## What To Avoid Initially

- High-frequency scalping. The account is too small, fees/spreads matter, and latency/adverse selection will dominate.
- LLM-only trading. Current open-source bots are useful references, but the benchmark evidence is not favorable for unchecked AI agents.
- Cross-market arbitrage as the first strategy. It needs capital on multiple venues, fast execution, and careful settlement/withdrawal/legal handling.
- Martingale or "double down" logic. It is incompatible with a $20 tranche.

