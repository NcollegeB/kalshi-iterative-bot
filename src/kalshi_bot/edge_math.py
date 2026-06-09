from __future__ import annotations

from dataclasses import dataclass

from .models import OutcomeSide
from .risk import expected_kalshi_fee_per_contract


@dataclass(frozen=True)
class EdgeBreakdown:
    probability_edge: float
    slippage_penalty: float
    fee_haircut: float
    edge_before_fees: float
    net_edge_after_costs: float


def edge_after_costs(
    *,
    probability_yes: float,
    outcome: OutcomeSide | str,
    executable_price: float,
    spread: float | None,
    base_slippage: float,
    spread_slippage_factor: float,
) -> EdgeBreakdown:
    outcome_value = outcome.value if isinstance(outcome, OutcomeSide) else str(outcome).lower()
    outcome_probability = probability_yes if outcome_value == "yes" else 1.0 - probability_yes
    probability_edge = outcome_probability - executable_price
    slippage = slippage_penalty(
        spread=spread,
        base_slippage=base_slippage,
        spread_slippage_factor=spread_slippage_factor,
    )
    fee = expected_kalshi_fee_per_contract(executable_price)
    edge_before_fees = probability_edge - slippage
    net_edge = edge_before_fees - fee
    return EdgeBreakdown(
        probability_edge=round(probability_edge, 10),
        slippage_penalty=round(slippage, 10),
        fee_haircut=round(fee, 10),
        edge_before_fees=round(edge_before_fees, 10),
        net_edge_after_costs=round(net_edge, 10),
    )


def slippage_penalty(
    *,
    spread: float | None,
    base_slippage: float,
    spread_slippage_factor: float,
) -> float:
    spread_component = max(float(spread or 0.0), 0.0) * max(float(spread_slippage_factor), 0.0)
    return max(float(base_slippage), 0.0) + spread_component
