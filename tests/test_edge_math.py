from kalshi_bot.edge_math import edge_after_costs
from kalshi_bot.models import OutcomeSide


def test_edge_after_costs_subtracts_fee_and_slippage():
    edge = edge_after_costs(
        probability_yes=0.70,
        outcome=OutcomeSide.YES,
        executable_price=0.50,
        spread=0.04,
        base_slippage=0.01,
        spread_slippage_factor=0.25,
    )

    assert edge.probability_edge == 0.20
    assert edge.slippage_penalty == 0.02
    assert edge.fee_haircut == 0.02
    assert edge.edge_before_fees == 0.18
    assert edge.net_edge_after_costs == 0.16
