from pathlib import Path

from kalshi_bot.dashboard import calibration_summary, parse_log_events, parse_note_metrics, read_probability_rows, tail_lines
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
