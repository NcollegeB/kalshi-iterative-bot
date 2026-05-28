from kalshi_bot.simulation import (
    SignalSample,
    SimulationParams,
    adjusted_probability,
    position_count,
    random_search,
    select_samples,
    simulate,
)


def sample(ticker="TEST", edge=0.12, price=0.4, probability=0.52):
    return SignalSample(
        signal_id=1,
        ticker=ticker,
        market_title="Test",
        outcome="yes",
        estimated_probability=probability,
        reference_price=price,
        edge=edge,
    )


def test_select_samples_filters_edge_and_multivariate():
    params = SimulationParams(min_edge_dollars=0.08, exclude_multivariate=True)
    selected = select_samples(
        [
            sample("LOWEDGE", edge=0.01),
            sample("KXMVE123", edge=0.2),
            sample("KEEP", edge=0.1),
        ],
        params,
    )
    assert [row.ticker for row in selected] == ["KEEP"]


def test_select_samples_prefers_highest_edge_under_risk_cap():
    params = SimulationParams(
        min_edge_dollars=0.01,
        max_position_dollars=1.0,
        max_open_risk_dollars=1.0,
        exclude_multivariate=True,
    )
    selected = select_samples(
        [
            sample("LOW", edge=0.02, price=0.5),
            sample("HIGH", edge=0.4, price=0.5),
        ],
        params,
    )
    assert [row.ticker for row in selected] == ["HIGH"]


def test_adjusted_probability_haircuts_edge_toward_price():
    params = SimulationParams(probability_haircut=0.5)
    row = sample(price=0.4, probability=0.6)
    assert adjusted_probability(row, params) == 0.5


def test_simulate_returns_loss_result():
    result = simulate([sample()], SimulationParams(exclude_multivariate=True), mc_trials=100, seed=7)
    assert result.selected_signals == 1
    assert result.avg_trades == 1
    assert isinstance(result.loss, float)


def test_position_count_respects_contract_cap():
    params = SimulationParams(max_position_dollars=2.0, max_contracts=25)
    assert position_count(sample(price=0.02), params) == 25


def test_random_search_excludes_multivariate_by_default():
    results = random_search([sample("KXMVE123", edge=0.2)], search_trials=5, mc_trials=5, seed=1)
    assert all(result.selected_signals == 0 for result in results)
