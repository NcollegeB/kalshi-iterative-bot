from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is optional for read-only scans
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "paper_trades.sqlite3"


PRODUCTION_REST_URL = "https://external-api.kalshi.com/trade-api/v2"
DEMO_REST_URL = "https://external-api.demo.kalshi.co/trade-api/v2"


@dataclass(frozen=True)
class KalshiConfig:
    environment: str
    rest_url: str
    api_key_id: str | None
    private_key_path: str | None
    private_key_pem: str | None
    allow_live: str

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key_id and (self.private_key_path or self.private_key_pem))


@dataclass(frozen=True)
class RiskConfig:
    bankroll_dollars: float = 20.0
    max_position_dollars: float = 1.0
    max_open_risk_dollars: float = 5.0
    daily_loss_limit_dollars: float = 2.0
    min_edge_dollars: float = 0.08
    max_bankroll_fraction_per_trade: float = 0.10
    kelly_fraction: float = 0.25
    min_contracts: float = 1.0
    max_contracts: float = 25.0
    adaptive_risk_enabled: bool = True
    adaptive_min_settled_trades: int = 20
    adaptive_window_trades: int = 50
    adaptive_step_up: float = 0.15
    adaptive_step_down: float = 0.25
    adaptive_min_multiplier: float = 0.50
    adaptive_max_multiplier: float = 1.25
    adaptive_max_brier_score: float = 0.25
    adaptive_max_log_loss: float = 0.75
    adaptive_max_drawdown_dollars: float = 4.0
    adaptive_min_net_pnl_dollars: float = 0.0
    adaptive_min_avg_clv: float = 0.0


@dataclass(frozen=True)
class AppConfig:
    kalshi: KalshiConfig
    risk: RiskConfig
    db_path: Path


def load_config(env_file: Path | None = None) -> AppConfig:
    if load_dotenv:
        load_dotenv(env_file or PROJECT_ROOT / ".env")

    environment = os.getenv("KALSHI_ENV", "production").strip().lower()
    if environment not in {"production", "demo"}:
        raise ValueError("KALSHI_ENV must be 'production' or 'demo'")

    rest_url = DEMO_REST_URL if environment == "demo" else PRODUCTION_REST_URL

    return AppConfig(
        kalshi=KalshiConfig(
            environment=environment,
            rest_url=rest_url,
            api_key_id=_empty_to_none(os.getenv("KALSHI_API_KEY_ID")),
            private_key_path=_empty_to_none(os.getenv("KALSHI_PRIVATE_KEY_PATH")),
            private_key_pem=_empty_to_none(os.getenv("KALSHI_PRIVATE_KEY_PEM")),
            allow_live=os.getenv("KALSHI_ALLOW_LIVE", "paper_only"),
        ),
        risk=RiskConfig(
            bankroll_dollars=_float_env("BOT_BANKROLL_DOLLARS", 20.0),
            max_position_dollars=_float_env("BOT_MAX_POSITION_DOLLARS", 1.0),
            max_open_risk_dollars=_float_env("BOT_MAX_OPEN_RISK_DOLLARS", 5.0),
            daily_loss_limit_dollars=_float_env("BOT_DAILY_LOSS_LIMIT_DOLLARS", 2.0),
            min_edge_dollars=_float_env("BOT_MIN_EDGE_DOLLARS", 0.08),
            max_bankroll_fraction_per_trade=_float_env("BOT_MAX_BANKROLL_FRACTION_PER_TRADE", 0.10),
            kelly_fraction=_float_env("BOT_KELLY_FRACTION", 0.25),
            adaptive_risk_enabled=_bool_env("BOT_ADAPTIVE_RISK_ENABLED", True),
            adaptive_min_settled_trades=_int_env("BOT_ADAPTIVE_MIN_SETTLED_TRADES", 20),
            adaptive_window_trades=_int_env("BOT_ADAPTIVE_WINDOW_TRADES", 50),
            adaptive_step_up=_float_env("BOT_ADAPTIVE_STEP_UP", 0.15),
            adaptive_step_down=_float_env("BOT_ADAPTIVE_STEP_DOWN", 0.25),
            adaptive_min_multiplier=_float_env("BOT_ADAPTIVE_MIN_MULTIPLIER", 0.50),
            adaptive_max_multiplier=_float_env("BOT_ADAPTIVE_MAX_MULTIPLIER", 1.25),
            adaptive_max_brier_score=_float_env("BOT_ADAPTIVE_MAX_BRIER_SCORE", 0.25),
            adaptive_max_log_loss=_float_env("BOT_ADAPTIVE_MAX_LOG_LOSS", 0.75),
            adaptive_max_drawdown_dollars=_float_env("BOT_ADAPTIVE_MAX_DRAWDOWN_DOLLARS", 4.0),
            adaptive_min_net_pnl_dollars=_float_env("BOT_ADAPTIVE_MIN_NET_PNL_DOLLARS", 0.0),
            adaptive_min_avg_clv=_float_env("BOT_ADAPTIVE_MIN_AVG_CLV", 0.0),
        ),
        db_path=Path(os.getenv("BOT_DB_PATH", str(DEFAULT_DB_PATH))).expanduser(),
    )


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return float(value)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return int(value)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
