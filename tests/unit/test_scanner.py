"""Atom A2 — Scanner orchestrator (issue #41)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from ross_trading.data.types import Bar, FloatRecord, Headline
from ross_trading.scanner.scanner import Scanner
from ross_trading.scanner.types import ScannerSnapshot

T0 = datetime(2026, 4, 26, 14, 30, tzinfo=UTC)


def _bar(*, symbol: str = "AVTX", close: str = "5.50", volume: int = 5_000_000) -> Bar:
    return Bar(
        symbol=symbol,
        ts=T0,
        timeframe="M1",
        open=Decimal("5.00"),
        high=Decimal(close),
        low=Decimal("4.95"),
        close=Decimal(close),
        volume=volume,
    )


def _float(shares: int, ticker: str = "AVTX") -> FloatRecord:
    return FloatRecord(
        ticker=ticker,
        as_of=date(2026, 4, 26),
        float_shares=shares,
        shares_outstanding=shares * 2,
        source="test",
    )


def _snap(
    *,
    symbol: str = "AVTX",
    close: str = "5.50",
    volume: int = 5_000_000,
    last: str = "5.50",
    prev_close: str = "5.00",
    baseline_30d: Decimal | None = Decimal("1_000_000"),
    float_shares: int | None = 8_500_000,
    headlines: tuple[Headline, ...] = (),
) -> ScannerSnapshot:
    return ScannerSnapshot(
        bar=_bar(symbol=symbol, close=close, volume=volume),
        last=Decimal(last),
        prev_close=Decimal(prev_close),
        baseline_30d=baseline_30d,
        float_record=_float(float_shares, symbol) if float_shares is not None else None,
        headlines=headlines,
    )


# -------------------------------------------------------------------- happy path


def test_passes_all_filters_yields_one_pick() -> None:
    scanner = Scanner()
    universe = frozenset(["AVTX"])
    snapshot = {"AVTX": _snap()}
    picks = scanner.scan(universe, snapshot)
    assert len(picks) == 1
    pick = picks[0]
    assert pick.ticker == "AVTX"
    assert pick.rank == 1
    assert pick.ts == T0
    assert pick.rel_volume == Decimal("5")
    assert pick.pct_change == Decimal("10")
    assert pick.price == Decimal("5.50")  # ScannerPick.price is snap.last
    assert pick.float_shares == 8_500_000
    assert pick.news_present is False
    assert pick.headline_count == 0


# ------------------------------------------------------------- universe handling


def test_universe_member_with_no_snapshot_is_skipped() -> None:
    scanner = Scanner()
    universe = frozenset(["AVTX", "BBAI"])
    snapshot = {"AVTX": _snap()}  # BBAI missing
    picks = scanner.scan(universe, snapshot)
    assert [p.ticker for p in picks] == ["AVTX"]


def test_empty_universe_yields_empty() -> None:
    scanner = Scanner()
    assert scanner.scan(frozenset(), {}) == []


# ------------------------------------------------------------------ each filter


def test_missing_baseline_rejects() -> None:
    scanner = Scanner()
    snapshot = {"AVTX": _snap(baseline_30d=None)}
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


def test_missing_float_record_rejects() -> None:
    scanner = Scanner()
    snapshot = {"AVTX": _snap(float_shares=None)}
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


def test_rel_volume_below_threshold_rejects() -> None:
    scanner = Scanner()  # default 5x
    snapshot = {"AVTX": _snap(volume=4_000_000)}  # 4x baseline
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


def test_pct_change_below_threshold_rejects() -> None:
    scanner = Scanner()  # default 10%
    snapshot = {"AVTX": _snap(last="5.40", prev_close="5.00")}  # +8%
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


def test_price_outside_band_rejects_low() -> None:
    scanner = Scanner()  # default [1, 20]
    snapshot = {"AVTX": _snap(close="0.50", last="0.55", prev_close="0.45")}
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


def test_price_outside_band_rejects_high() -> None:
    scanner = Scanner()
    snapshot = {"AVTX": _snap(close="25.00", last="25.50", prev_close="22.00")}
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


def test_float_above_threshold_rejects() -> None:
    scanner = Scanner()  # default 20M
    snapshot = {"AVTX": _snap(float_shares=25_000_000)}
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


# ---------------------------------------------------------------- non-gating news


def test_news_recorded_but_not_gating() -> None:
    """Per D5/#39: news_present is recorded on the pick but does NOT gate selection."""
    scanner = Scanner()
    headlines = (
        Headline(ticker="AVTX", ts=T0 - timedelta(hours=1), source="Benzinga", title="story"),
    )
    snap_with_news = _snap(headlines=headlines)
    snap_without_news = _snap()
    picks_with = scanner.scan(frozenset(["AVTX"]), {"AVTX": snap_with_news})
    picks_without = scanner.scan(frozenset(["AVTX"]), {"AVTX": snap_without_news})
    # Both qualify (news non-gating); both produce one pick.
    assert len(picks_with) == 1
    assert len(picks_without) == 1
    assert picks_with[0].news_present is True
    assert picks_with[0].headline_count == 1
    assert picks_without[0].news_present is False
    assert picks_without[0].headline_count == 0


# ---------------------------------------------------------------------- top-N


def test_top_n_truncates_and_orders_by_pct_change() -> None:
    scanner = Scanner()  # default n=5
    universe = frozenset(["A", "B", "C", "D", "E", "F", "G"])
    pct_changes = {"A": 10, "B": 20, "C": 15, "D": 11, "E": 25, "F": 12, "G": 18}
    snapshot = {}
    for sym, pct in pct_changes.items():
        new_last = Decimal("5.00") + Decimal("5.00") * Decimal(pct) / Decimal("100")
        snapshot[sym] = _snap(symbol=sym, last=str(new_last), prev_close="5.00")
    picks = scanner.scan(universe, snapshot)
    assert [p.ticker for p in picks] == ["E", "B", "G", "C", "F"]
    assert [p.rank for p in picks] == [1, 2, 3, 4, 5]


# --------------------------------------------------------------- determinism


def test_deterministic_same_inputs_same_output() -> None:
    scanner = Scanner()
    universe = frozenset(["AVTX", "BBAI"])
    snapshot = {
        "AVTX": _snap(symbol="AVTX"),
        "BBAI": _snap(symbol="BBAI", last="5.55"),
    }
    out_a = scanner.scan(universe, snapshot)
    out_b = scanner.scan(universe, snapshot)
    assert out_a == out_b


# ------------------------------------------------------------ custom thresholds


def test_custom_thresholds_let_a_b_test_without_surgery() -> None:
    scanner = Scanner(rel_volume_threshold=10.0)  # tighter rel-vol
    snapshot = {"AVTX": _snap(volume=4_000_000)}  # 4x — fails 10x cutoff
    assert scanner.scan(frozenset(["AVTX"]), snapshot) == []


def test_custom_top_n() -> None:
    scanner = Scanner(top_n=2)
    universe = frozenset(["A", "B", "C"])
    snapshot = {
        sym: _snap(symbol=sym, last=str(Decimal("5") + Decimal(i)), prev_close="5.00")
        for i, sym in enumerate(universe, start=1)
    }
    picks = scanner.scan(universe, snapshot)
    assert len(picks) == 2
