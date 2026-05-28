from pathlib import Path

from kalshi_bot.dashboard import (
    calibration_summary,
    horizon_bucket,
    parse_log_events,
    parse_note_metrics,
    performance_breakdown,
    read_probability_rows,
    spread_bucket,
    tail_lines,
)
from kalshi_bot.ledger import PaperLedger
from kalshi_bot.models import BookSide, OutcomeSide, ProposedOrder, Signal, TradeMode


def test_parse_note_metrics_extracts_model_inputs():
    metrics = parse_note_metrics(
        "ETH crypto model side=yes edge=0.1179 spot=1990.56 strike=2059.99 raw_edge=0.0094"
    )

    assert metrics["side"] == "yes"
    assert metrics["asset"] == "ETH"
    assert metrics["edge"] == 0.1179
    assert metrics["spot"] == 1990.56
    assert metrics["raw_edge"] == 0.0094


def test_read_probability_rows_parses_csv(tmp_path: Path):
    path = tmp_path / "probabilities.csv"
    path.write_text(
        "ticker,estimated_probability,notes\n"
        "KXETHD-TEST,0.24,ETH crypto model side=yes edge=0.12 spot=1991\n"
    )

    rows = read_probability_rows(path)

    assert rows[0]["ticker"] == "KXETHD-TEST"
    assert rows[0]["estimated_probability"] == 0.24
    assert rows[0]["metrics"]["edge"] == 0.12


def test_parse_log_events_handles_python_dict_lines(tmp_path: Path):
    path = tmp_path / "live.log"
    path.write_text("noise\n{'loop_iteration': 7, 'signals': 1}\n")

    assert parse_log_events(path, limit=10) == [{"loop_iteration": 7, "signals": 1}]


def test_tail_lines_reads_only_requested_suffix(tmp_path: Path):
    path = tmp_path / "live.log"
    path.write_text("\n".join(str(index) for index in range(20)) + "\n")

    assert tail_lines(path, limit=3) == ["17", "18", "19"]


def test_calibration_summary_scores_settled_predictions(tmp_path: Path):
    ledger = PaperLedger(tmp_path / "ledger.sqlite3")
    signal = Signal.now(
        strategy="test",
        ticker="T",
        market_title="test",
        outcome=OutcomeSide.YES,
        estimated_probability=0.75,
        reference_price=0.5,
        edge=0.25,
        reason="test",
        asset="BTC",
    )
    signal_id = ledger.record_signal(signal, TradeMode.PAPER, "approved", "ok")
    order_id = ledger.record_order(
        signal_id,
        ProposedOrder(
            ticker="T",
            book_side=BookSide.BID,
            outcome=OutcomeSide.YES,
            count=1,
            price=0.5,
            client_order_id="cid",
        ),
        TradeMode.PAPER,
        "paper_open",
    )
    ledger.mark_paper_settled(
        order_id=order_id,
        outcome_result="yes",
        settlement_value=1.0,
        settled_at="2026-05-28T17:00:00Z",
        gross_pnl_dollars=0.5,
        fee_estimate_dollars=0.0,
        net_pnl_dollars=0.5,
    )

    summary = calibration_summary(tmp_path / "ledger.sqlite3")

    assert summary["overall"]["count"] == 1
    assert summary["overall"]["brier_score"] == 0.0625


def test_performance_breakdown_groups_realized_orders(tmp_path: Path):
    ledger = PaperLedger(tmp_path / "ledger.sqlite3")
    btc_signal = Signal.now(
        strategy="test",
        ticker="KXBTCD-TEST",
        market_title="test",
        outcome=OutcomeSide.YES,
        estimated_probability=0.75,
        reference_price=0.5,
        edge=0.25,
        reason="test",
        asset="BTC",
        spread=0.01,
        time_to_close_minutes=20,
    )
    eth_signal = Signal.now(
        strategy="test",
        ticker="KXETHD-TEST",
        market_title="test",
        outcome=OutcomeSide.NO,
        estimated_probability=0.6,
        reference_price=0.4,
        edge=0.2,
        reason="test",
        asset="ETH",
        spread=0.08,
        time_to_close_minutes=300,
    )
    btc_signal_id = ledger.record_signal(btc_signal, TradeMode.PAPER, "approved", "ok")
    eth_signal_id = ledger.record_signal(eth_signal, TradeMode.PAPER, "approved", "ok")
    btc_order_id = ledger.record_order(
        btc_signal_id,
        ProposedOrder("KXBTCD-TEST", BookSide.BID, OutcomeSide.YES, 1, 0.5, "btc-cid"),
        TradeMode.PAPER,
        "paper_open",
    )
    eth_order_id = ledger.record_order(
        eth_signal_id,
        ProposedOrder("KXETHD-TEST", BookSide.BID, OutcomeSide.NO, 1, 0.4, "eth-cid"),
        TradeMode.PAPER,
        "paper_open",
    )
    ledger.mark_paper_settled(
        order_id=btc_order_id,
        outcome_result="yes",
        settlement_value=1.0,
        settled_at="2026-05-28T17:00:00Z",
        gross_pnl_dollars=0.5,
        fee_estimate_dollars=0.0,
        net_pnl_dollars=0.5,
    )
    ledger.mark_paper_settled(
        order_id=eth_order_id,
        outcome_result="no",
        settlement_value=0.0,
        settled_at="2026-05-28T17:01:00Z",
        gross_pnl_dollars=0.6,
        fee_estimate_dollars=0.0,
        net_pnl_dollars=0.6,
    )

    report = performance_breakdown(tmp_path / "ledger.sqlite3")

    assert report["mode_filter"] == "all"
    assert report["overall"]["count"] == 2
    assert report["overall"]["net_pnl"] == 1.1
    assert report["overall"]["final_count"] == 2
    assert {row["bucket"] for row in report["by_mode"]} == {"paper"}
    assert {row["bucket"] for row in report["by_asset"]} == {"BTC", "ETH"}
    assert {row["bucket"] for row in report["by_side"]} == {"yes", "no"}
    assert {row["bucket"] for row in report["by_horizon"]} == {"10-30m", "4-24h"}
    assert {row["bucket"] for row in report["by_spread"]} == {"<=2c", "5-10c"}

    live_report = performance_breakdown(tmp_path / "ledger.sqlite3", mode="live")
    assert live_report["mode_filter"] == "live"
    assert live_report["overall"]["count"] == 0


def test_bucket_helpers():
    assert horizon_bucket(5) == "<=10m"
    assert horizon_bucket(90) == "1-4h"
    assert horizon_bucket(None) == "unknown"
    assert spread_bucket(0.01) == "<=2c"
    assert spread_bucket(0.06) == "5-10c"
    assert spread_bucket(None) == "unknown"
