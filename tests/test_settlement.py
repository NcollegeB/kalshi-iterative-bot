from kalshi_bot.settlement import calculate_paper_settlement, result_label


def test_yes_settlement_profit_net_of_fee_estimate():
    settlement = calculate_paper_settlement(
        outcome="yes",
        count=10,
        entry_price=0.40,
        settlement_value=1.0,
    )
    assert settlement.outcome_result == "yes"
    assert settlement.gross_pnl_dollars == 6.0
    assert settlement.fee_estimate_dollars > 0
    assert settlement.net_pnl_dollars < settlement.gross_pnl_dollars


def test_no_settlement_profit_uses_inverse_value():
    settlement = calculate_paper_settlement(
        outcome="no",
        count=10,
        entry_price=0.30,
        settlement_value=0.0,
        include_fee_estimate=False,
    )
    assert settlement.outcome_result == "no"
    assert settlement.payout_per_contract == 1.0
    assert settlement.net_pnl_dollars == 7.0


def test_result_label_handles_scalar_values():
    assert result_label(1.0) == "yes"
    assert result_label(0.0) == "no"
    assert result_label(0.42) == "scalar"

