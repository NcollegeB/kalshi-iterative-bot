from __future__ import annotations

from dataclasses import dataclass
from math import floor

from .config import RiskConfig
from .models import RiskDecision, Signal


@dataclass(frozen=True)
class PortfolioState:
    bankroll_dollars: float
    open_risk_dollars: float
    realized_pnl_today_dollars: float = 0.0


class RiskManager:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config

    def evaluate(self, signal: Signal, state: PortfolioState) -> RiskDecision:
        if signal.edge < self.config.min_edge_dollars:
            return RiskDecision(False, f"edge {signal.edge:.4f} below minimum {self.config.min_edge_dollars:.4f}")

        if state.realized_pnl_today_dollars <= -self.config.daily_loss_limit_dollars:
            return RiskDecision(False, "daily loss circuit breaker is active")

        remaining_open_risk = self.config.max_open_risk_dollars - state.open_risk_dollars
        if remaining_open_risk <= 0:
            return RiskDecision(False, "max open risk reached")

        per_position_budget = min(
            self.config.max_position_dollars,
            state.bankroll_dollars * self.config.max_bankroll_fraction_per_trade,
            remaining_open_risk,
            state.bankroll_dollars,
        )
        kelly_budget = self._fractional_kelly_budget(signal, state)
        if kelly_budget is not None:
            per_position_budget = min(per_position_budget, kelly_budget)
        if per_position_budget <= 0:
            return RiskDecision(False, "no bankroll available")

        price = max(signal.reference_price, 0.0001)
        count = floor((per_position_budget / price) * 100) / 100
        count = min(count, self.config.max_contracts)
        if count < self.config.min_contracts:
            return RiskDecision(False, "position would be smaller than minimum contract count")

        max_loss = round(count * price, 4)
        return RiskDecision(True, "approved", count=count, max_loss_dollars=max_loss)

    def _fractional_kelly_budget(self, signal: Signal, state: PortfolioState) -> float | None:
        if self.config.kelly_fraction <= 0:
            return None
        price = signal.reference_price
        probability = signal.estimated_probability
        if not (0 < price < 1 and 0 < probability < 1):
            return 0.0
        full_kelly_stake_fraction = max(0.0, (probability - price) / (1.0 - price))
        return state.bankroll_dollars * self.config.kelly_fraction * full_kelly_stake_fraction
