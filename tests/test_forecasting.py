import pytest

from kalshi_bot.forecasting import blend_probability_with_market, market_yes_probability, model_market_gap


def test_market_yes_probability_uses_bid_ask_midpoint():
    probability = market_yes_probability(yes_bid=0.30, yes_ask=0.42, no_bid=0.58, no_ask=0.70)

    assert probability == 0.36


def test_market_blend_pulls_model_toward_market():
    blended = blend_probability_with_market(0.80, 0.40, market_weight=0.25)

    assert blended == pytest.approx(0.70)


def test_model_market_gap_is_absolute_difference():
    assert model_market_gap(0.80, 0.40) == 0.4
    assert model_market_gap(0.80, None) is None
