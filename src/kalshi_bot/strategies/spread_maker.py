from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from kalshi_bot.kalshi_client import KalshiApiError, KalshiClient
from kalshi_bot.models import Market, OutcomeSide, Signal


class SpreadMakerPaperStrategy:
    """Paper-only baseline for collecting liquidity-provision data.

    This does not claim predictive edge. It proposes a YES bid inside wide spreads
    so fills/outcomes can be studied before deploying a real model.
    """

    name = "spread_maker_paper"

    def __init__(
        self,
        min_spread: float = 0.10,
        max_price: float = 0.75,
        min_volume: float = 100.0,
        exclude_multivariate: bool = True,
        workers: int = 6,
    ) -> None:
        self.min_spread = min_spread
        self.max_price = max_price
        self.min_volume = min_volume
        self.exclude_multivariate = exclude_multivariate
        self.workers = max(1, workers)

    def generate(self, client: KalshiClient, markets: list[Market]) -> list[Signal]:
        candidates = [
            market
            for market in markets
            if not (self.exclude_multivariate and market.ticker.startswith("KXMVE"))
            and market.volume >= self.min_volume
        ]

        if self.workers == 1:
            return [signal for market in candidates if (signal := self._generate_for_market(client, market))]

        signals: list[Signal] = []
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = [executor.submit(self._generate_for_market, client, market) for market in candidates]
            for future in as_completed(futures):
                signal = future.result()
                if signal:
                    signals.append(signal)
        return sorted(signals, key=lambda signal: signal.ticker)

    def _generate_for_market(self, client: KalshiClient, market: Market) -> Signal | None:
        try:
            top = client.get_orderbook(market.ticker)
        except KalshiApiError:
            return None
        if top.yes_bid is None or top.yes_ask is None or top.yes_spread is None:
            return None
        if top.yes_spread < self.min_spread or top.yes_bid > self.max_price:
            return None
        proposed_bid = round(min(top.yes_bid + 0.01, top.yes_ask - 0.01), 4)
        if proposed_bid <= 0 or proposed_bid >= 1:
            return None
        estimated_probability = round((top.yes_bid + top.yes_ask) / 2, 4)
        return Signal.now(
            strategy=self.name,
            ticker=market.ticker,
            market_title=market.title,
            outcome=OutcomeSide.YES,
            estimated_probability=estimated_probability,
            reference_price=proposed_bid,
            edge=round(estimated_probability - proposed_bid, 4),
            reason=(
                f"paper maker quote inside {top.yes_spread:.2%} YES spread; "
                "not a proven edge"
            )
        )
