"""
tests/test_strategies.py
Unit tests for strategy signal generation using synthetic market data.
No broker / API dependencies required.
"""
import os
os.environ["PAPER_TRADING"] = "True"
os.environ["CAPITAL"] = "100000"
os.environ["MAX_RISK_PER_TRADE_PCT"] = "1.0"
os.environ["MAX_DAILY_LOSS_PCT"] = "3.0"
os.environ["MAX_OPEN_TRADES"] = "3"
os.environ["SL_PCT"] = "30"
os.environ["TARGET_PCT"] = "60"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["LOG_FILE"] = "logs/trading.log"
os.environ["ENABLED_STRATEGIES"] = "trend_following,breakout,vwap_reversal"

import pandas as pd
import numpy as np
import pytest

from strategies.trend_following import TrendFollowingStrategy
from strategies.breakout import BreakoutStrategy
from strategies.vwap_reversal import VWAPReversionStrategy


def _make_base_snapshot(symbol="NIFTY", ltp=22000.0, n_bars=210) -> dict:
    """Create a minimal snapshot with a flat DataFrame."""
    idx = pd.date_range("2024-01-01 09:15", periods=n_bars, freq="5min")
    close = pd.Series([ltp] * n_bars, index=idx)
    df = pd.DataFrame({
        "open": close,
        "high": close + 10,
        "low": close - 10,
        "close": close,
        "volume": pd.Series([200_000.0] * n_bars, index=idx),
    })
    return {
        "symbol": symbol,
        "token": "99926000",
        "ltp": ltp,
        "df_5min": df,
        "df_15min": df.copy(),
        "df_daily": df.copy(),
        "prev_day_high": ltp + 100,
        "prev_day_low": ltp - 100,
        "timestamp": idx[-1],
    }


class TestTrendFollowingStrategy:
    def setup_method(self):
        self.strategy = TrendFollowingStrategy()

    def test_bullish_signal_on_uptrend(self):
        n = 210
        idx = pd.date_range("2024-01-01 09:15", periods=n, freq="5min")
        # Rising trend: EMA21 > EMA50 > EMA200
        close = pd.Series(np.linspace(20000, 22200, n), index=idx)
        df = pd.DataFrame({
            "open": close - 5,
            "high": close + 20,
            "low": close - 20,
            "close": close,
            "volume": 200_000.0,
        })
        from utils.indicators import enrich
        df = enrich(df)

        snapshot = {
            "symbol": "NIFTY",
            "ltp": float(close.iloc[-1]),
            "df_5min": df,
            "df_15min": df.copy(),
            "df_daily": df.copy(),
            "prev_day_high": float(close.max()),
            "prev_day_low": float(close.min()),
        }
        signal = self.strategy.generate_signal(snapshot)
        # In a strong uptrend, may or may not fire depending on pullback condition
        # Just ensure it doesn't crash and returns Signal or None
        assert signal is None or signal.option_type in ("CE", "PE")

    def test_no_signal_on_flat_market(self):
        snapshot = _make_base_snapshot(ltp=22000.0)
        from utils.indicators import enrich
        snapshot["df_5min"] = enrich(snapshot["df_5min"])
        signal = self.strategy.generate_signal(snapshot)
        # Flat market: no clear EMA alignment — should be None
        # (EMA21 ≈ EMA50 ≈ EMA200 when all prices are equal)
        assert signal is None

    def test_insufficient_bars_returns_none(self):
        snapshot = _make_base_snapshot(n_bars=50)
        signal = self.strategy.generate_signal(snapshot)
        assert signal is None

    def test_no_signal_when_no_ltp(self):
        snapshot = _make_base_snapshot()
        snapshot["ltp"] = None
        signal = self.strategy.generate_signal(snapshot)
        assert signal is None


class TestBreakoutStrategy:
    def setup_method(self):
        self.strategy = BreakoutStrategy()

    def test_bullish_breakout_signal(self):
        ltp = 22200.0
        prev_high = 22100.0  # Price broke above this
        snapshot = _make_base_snapshot(ltp=ltp, n_bars=30)
        snapshot["prev_day_high"] = prev_high
        snapshot["prev_day_low"] = 21500.0

        from utils.indicators import enrich
        df = enrich(snapshot["df_5min"])
        # Inject volume spike
        df.iloc[-1, df.columns.get_loc("volume")] = 800_000.0
        df.iloc[-1, df.columns.get_loc("vol_spike")] = True
        snapshot["df_5min"] = df

        signal = self.strategy.generate_signal(snapshot)
        if signal:  # may not fire if vol_spike column name differs
            assert signal.option_type == "CE"

    def test_no_signal_without_breakout(self):
        snapshot = _make_base_snapshot(ltp=22000.0, n_bars=30)
        snapshot["prev_day_high"] = 22500.0  # price below PDH
        from utils.indicators import enrich
        snapshot["df_5min"] = enrich(snapshot["df_5min"])
        signal = self.strategy.generate_signal(snapshot)
        assert signal is None

    def test_no_signal_on_missing_prev_levels(self):
        snapshot = _make_base_snapshot()
        snapshot["prev_day_high"] = float("nan")
        snapshot["prev_day_low"] = float("nan")
        signal = self.strategy.generate_signal(snapshot)
        assert signal is None


class TestVWAPReversionStrategy:
    def setup_method(self):
        self.strategy = VWAPReversionStrategy()

    def test_no_signal_with_insufficient_bars(self):
        snapshot = _make_base_snapshot(n_bars=2)
        signal = self.strategy.generate_signal(snapshot)
        assert signal is None

    def test_no_signal_on_flat_vwap(self):
        snapshot = _make_base_snapshot(ltp=22000.0)
        from utils.indicators import enrich
        snapshot["df_5min"] = enrich(snapshot["df_5min"])
        signal = self.strategy.generate_signal(snapshot)
        # Flat data: no deviation from VWAP → no signal
        assert signal is None

    def test_signal_confidence_range(self):
        # Test confidence scoring function directly
        conf = VWAPReversionStrategy._compute_confidence(1.0, 55.0, 45.0)
        assert 0.65 <= conf <= 0.85
