from __future__ import annotations

import random
from dataclasses import dataclass
from math import ceil, floor
from statistics import mean, median


@dataclass(frozen=True)
class SignalSample:
    signal_id: int
    ticker: str
    market_title: str
    outcome: str
    estimated_probability: float
    reference_price: float
    edge: float


@dataclass(frozen=True)
class SimulationParams:
    bankroll_dollars: float = 20.0
    min_edge_dollars: float = 0.08
    max_position_dollars: float = 1.0
    max_open_risk_dollars: float = 5.0
    max_contracts: float = 25.0
    probability_haircut: float = 0.80
    exclude_multivariate: bool = True
    fee_rate: float = 0.07


@dataclass(frozen=True)
class SimulationResult:
    params: SimulationParams
    selected_signals: int
    avg_trades: float
    mean_pnl: float
    median_pnl: float
    cvar_5_pnl: float
    loss: float


def simulate(
    samples: list[SignalSample],
    params: SimulationParams,
    *,
    mc_trials: int,
    seed: int = 1,
) -> SimulationResult:
    selected = select_samples(samples, params)
    rng = random.Random(seed)
    pnls: list[float] = []
    trade_counts: list[int] = []
    for _ in range(mc_trials):
        pnl, trades = simulate_once(selected, params, rng)
        pnls.append(pnl)
        trade_counts.append(trades)

    if not pnls:
        pnls = [0.0]
        trade_counts = [0]

    sorted_pnls = sorted(pnls)
    tail_count = max(1, ceil(len(sorted_pnls) * 0.05))
    cvar_5 = mean(sorted_pnls[:tail_count])
    mean_p = mean(pnls)
    med_p = median(pnls)
    avg_trades = mean(trade_counts)
    loss = calculate_loss(mean_pnl=mean_p, cvar_5_pnl=cvar_5, avg_trades=avg_trades)
    return SimulationResult(
        params=params,
        selected_signals=len(selected),
        avg_trades=avg_trades,
        mean_pnl=mean_p,
        median_pnl=med_p,
        cvar_5_pnl=cvar_5,
        loss=loss,
    )


def random_search(
    samples: list[SignalSample],
    *,
    search_trials: int,
    mc_trials: int,
    seed: int = 1,
    top_n: int = 10,
    allow_multivariate: bool = False,
) -> list[SimulationResult]:
    rng = random.Random(seed)
    results: list[SimulationResult] = []
    for trial in range(search_trials):
        params = random_params(rng, allow_multivariate=allow_multivariate)
        result = simulate(samples, params, mc_trials=mc_trials, seed=seed + trial + 1)
        results.append(result)
    return sorted(results, key=lambda result: result.loss)[:top_n]


def select_samples(samples: list[SignalSample], params: SimulationParams) -> list[SignalSample]:
    selected: list[SignalSample] = []
    open_risk = 0.0
    for sample in sorted(samples, key=lambda row: row.edge, reverse=True):
        if params.exclude_multivariate and sample.ticker.startswith("KXMVE"):
            continue
        if sample.edge < params.min_edge_dollars:
            continue
        position_risk = position_max_loss(sample, params)
        if position_risk <= 0:
            continue
        if open_risk + position_risk > params.max_open_risk_dollars:
            continue
        selected.append(sample)
        open_risk += position_risk
    return selected


def simulate_once(
    samples: list[SignalSample],
    params: SimulationParams,
    rng: random.Random,
) -> tuple[float, int]:
    total_pnl = 0.0
    trades = 0
    for sample in samples:
        count = position_count(sample, params)
        if count <= 0:
            continue
        win_probability = adjusted_probability(sample, params)
        won = rng.random() < win_probability
        gross_pnl = count * (1.0 - sample.reference_price) if won else -(count * sample.reference_price)
        fees = estimate_taker_fee(sample.reference_price, count, params.fee_rate)
        total_pnl += gross_pnl - fees
        trades += 1
    return round(total_pnl, 4), trades


def adjusted_probability(sample: SignalSample, params: SimulationParams) -> float:
    price = clamp(sample.reference_price, 0.001, 0.999)
    probability = clamp(sample.estimated_probability, 0.001, 0.999)
    edge = probability - price
    return clamp(price + edge * (1.0 - params.probability_haircut), 0.001, 0.999)


def position_count(sample: SignalSample, params: SimulationParams) -> float:
    if sample.reference_price <= 0:
        return 0.0
    budget = min(params.max_position_dollars, params.bankroll_dollars)
    count = floor((budget / sample.reference_price) * 100) / 100
    return min(count, params.max_contracts)


def position_max_loss(sample: SignalSample, params: SimulationParams) -> float:
    count = position_count(sample, params)
    if count <= 0:
        return 0.0
    return round(count * sample.reference_price, 4)


def estimate_taker_fee(price: float, count: float, fee_rate: float) -> float:
    raw_fee = fee_rate * count * price * (1.0 - price)
    return ceil(raw_fee * 10_000) / 10_000


def calculate_loss(*, mean_pnl: float, cvar_5_pnl: float, avg_trades: float) -> float:
    downside_penalty = max(0.0, -cvar_5_pnl) * 1.5
    inactivity_penalty = 0.25 if avg_trades == 0 else 0.0
    churn_penalty = avg_trades * 0.01
    return round(-mean_pnl + downside_penalty + inactivity_penalty + churn_penalty, 6)


def random_params(rng: random.Random, *, allow_multivariate: bool = False) -> SimulationParams:
    return SimulationParams(
        bankroll_dollars=20.0,
        min_edge_dollars=round(rng.uniform(0.02, 0.20), 4),
        max_position_dollars=round(rng.uniform(0.25, 2.00), 2),
        max_open_risk_dollars=round(rng.uniform(1.0, 8.0), 2),
        probability_haircut=round(rng.uniform(0.60, 0.98), 3),
        exclude_multivariate=True if not allow_multivariate else rng.random() < 0.70,
    )


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
