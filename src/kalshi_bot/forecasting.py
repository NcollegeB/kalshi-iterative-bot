from __future__ import annotations


def blend_probability_with_market(
    model_probability: float,
    market_probability: float | None,
    *,
    market_weight: float,
) -> float:
    model_probability = _bounded_probability(model_probability)
    if market_probability is None:
        return model_probability
    weight = min(max(float(market_weight), 0.0), 1.0)
    market_probability = _bounded_probability(market_probability)
    return _bounded_probability((1.0 - weight) * model_probability + weight * market_probability)


def market_yes_probability(
    *,
    yes_bid: float | None,
    yes_ask: float | None,
    no_bid: float | None,
    no_ask: float | None,
) -> float | None:
    bid = yes_bid if yes_bid is not None else _complement(no_ask)
    ask = yes_ask if yes_ask is not None else _complement(no_bid)
    if bid is not None and ask is not None:
        return _bounded_probability((bid + ask) / 2.0)
    if bid is not None:
        return _bounded_probability(bid)
    if ask is not None:
        return _bounded_probability(ask)
    return None


def model_market_gap(model_probability: float, market_probability: float | None) -> float | None:
    if market_probability is None:
        return None
    return abs(_bounded_probability(model_probability) - _bounded_probability(market_probability))


def _bounded_probability(value: float) -> float:
    return max(0.001, min(0.999, float(value)))


def _complement(value: float | None) -> float | None:
    if value is None:
        return None
    return 1.0 - float(value)
