from __future__ import annotations

from dataclasses import dataclass

from .models import OutcomeSide
from .simulation import estimate_taker_fee


@dataclass(frozen=True)
class PaperSettlement:
    outcome_result: str
    settlement_value: float
    payout_per_contract: float
    gross_pnl_dollars: float
    fee_estimate_dollars: float
    net_pnl_dollars: float


def calculate_paper_settlement(
    *,
    outcome: str,
    count: float,
    entry_price: float,
    settlement_value: float,
    include_fee_estimate: bool = True,
) -> PaperSettlement:
    normalized_outcome = OutcomeSide(outcome)
    value = max(0.0, min(1.0, settlement_value))
    payout = value if normalized_outcome == OutcomeSide.YES else 1.0 - value
    gross_pnl = count * (payout - entry_price)
    fee_estimate = estimate_taker_fee(entry_price, count, 0.07) if include_fee_estimate else 0.0
    net_pnl = gross_pnl - fee_estimate
    return PaperSettlement(
        outcome_result=result_label(value),
        settlement_value=round(value, 4),
        payout_per_contract=round(payout, 4),
        gross_pnl_dollars=round(gross_pnl, 4),
        fee_estimate_dollars=round(fee_estimate, 4),
        net_pnl_dollars=round(net_pnl, 4),
    )


def result_label(settlement_value: float) -> str:
    if settlement_value >= 0.9999:
        return "yes"
    if settlement_value <= 0.0001:
        return "no"
    return "scalar"

