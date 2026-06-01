from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import floor

from .adaptive_risk import effective_risk_limits
from .config import RiskConfig
from .models import RiskDecision, Signal


@dataclass(frozen=True)
class PortfolioState:
    bankroll_dollars: float
    open_risk_dollars: float
    realized_pnl_today_dollars: float = 0.0


class RiskManager:
    def __init__(
        self,
        config: RiskConfig,
        risk_multiplier: float = 1.0,
        blocked_assets: Iterable[str] | None = None,
    ) -> None:
        self.config = config
        self.risk_multiplier = max(0.0, risk_multiplier)
        self.effective_limits = effective_risk_limits(config, self.risk_multiplier)
        self.blocked_assets = {asset.upper() for asset in blocked_assets or ()}

    def evaluate(self, signal: Signal, state: PortfolioState) -> RiskDecision:
        if signal.edge < self.config.min_edge_dollars:
            return RiskDecision(False, f"edge {signal.edge:.4f} below minimum {self.config.min_edge_dollars:.4f}")

        asset = (signal.asset or "").upper()
        if asset and asset in self.blocked_assets:
            return RiskDecision(False, f"asset {asset} blocked by recent performance guard")

        if self.config.allowed_assets:
            if asset not in self.config.allowed_assets:
                allowed = ",".join(self.config.allowed_assets)
                return RiskDecision(False, f"asset {asset or 'unknown'} outside allowed assets {allowed}")

        if self.config.max_spread_dollars is not None:
            if signal.spread is None:
                return RiskDecision(False, "spread unavailable")
            if signal.spread > self.config.max_spread_dollars:
                return RiskDecision(
                    False,
                    f"spread {signal.spread:.4f} above maximum {self.config.max_spread_dollars:.4f}",
                )

        if self.config.min_time_to_close_minutes is not None:
            if signal.time_to_close_minutes is None:
                return RiskDecision(False, "time-to-close unavailable")
            if signal.time_to_close_minutes < self.config.min_time_to_close_minutes:
                return RiskDecision(
                    False,
                    (
                        f"time-to-close {signal.time_to_close_minutes:.2f}m below minimum "
                        f"{self.config.min_time_to_close_minutes:.2f}m"
                    ),
                )

        if self.config.max_time_to_close_minutes is not None:
            if signal.time_to_close_minutes is None:
                return RiskDecision(False, "time-to-close unavailable")
            if signal.time_to_close_minutes > self.config.max_time_to_close_minutes:
                return RiskDecision(
                    False,
                    (
                        f"time-to-close {signal.time_to_close_minutes:.2f}m above maximum "
                        f"{self.config.max_time_to_close_minutes:.2f}m"
                    ),
                )

        daily_loss_limit = self.effective_limits["daily_loss_limit_dollars"]
        if state.realized_pnl_today_dollars <= -daily_loss_limit:
            return RiskDecision(False, "daily loss circuit breaker is active")

        max_open_risk = self.effective_limits["max_open_risk_dollars"]
        remaining_open_risk = max_open_risk - state.open_risk_dollars
        if remaining_open_risk <= 0:
            return RiskDecision(False, "max open risk reached")

        per_position_budget = min(
            self.effective_limits["max_position_dollars"],
            state.bankroll_dollars * self.effective_limits["max_bankroll_fraction_per_trade"],
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
        kelly_fraction = self.effective_limits["kelly_fraction"]
        if kelly_fraction <= 0:
            return None
        price = signal.reference_price
        probability = signal.estimated_probability
        if not (0 < price < 1 and 0 < probability < 1):
            return 0.0
        full_kelly_stake_fraction = max(0.0, (probability - price) / (1.0 - price))
        return state.bankroll_dollars * kelly_fraction * full_kelly_stake_fraction
