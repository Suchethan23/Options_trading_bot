"""
tests/test_indicators.py
Unit tests for technical indicator functions.
No API keys or broker connection required.
"""
import pandas as pd
import numpy as np
import pytest

from utils.indicators import (
    ema, sma, rsi, atr, bollinger_bands, volume_spike,
    vwap_simple, enrich, prev_day_levels
)


def _make_df(n=50, base=22000.0, seed=42) -> pd.DataFrame:
    """Create synthetic OHLCV DataFrame for testing."""
    rng = np.random.default_rng(seed)
    closes = base + rng.normal(0, 50, n).cumsum()
    highs = closes + rng.uniform(5, 30, n)
    lows = closes - rng.uniform(5, 30, n)
    opens = closes + rng.normal(0, 20, n)
    volumes = rng.integers(100_000, 500_000, n).astype(float)
    idx = pd.date_range("2024-01-01 09:15", periods=n, freq="5min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


class TestEMA:
    def test_ema_length(self):
        df = _make_df(50)
        result = ema(df["close"], 9)
        assert len(result) == 50

    def test_ema_no_nan_after_period(self):
        df = _make_df(50)
        result = ema(df["close"], 9)
        assert not result.iloc[9:].isna().any()

    def test_ema_follows_trend(self):
        # Rising series → EMA9 > EMA21 eventually
        s = pd.Series(range(1, 51, 1), dtype=float)
        e9 = ema(s, 9)
        e21 = ema(s, 21)
        assert e9.iloc[-1] > e21.iloc[-1]


class TestRSI:
    def test_rsi_range(self):
        df = _make_df(60)
        result = rsi(df["close"], 14)
        valid = result.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_rsi_overbought_falling_series(self):
        s = pd.Series([100 - i * 0.5 for i in range(60)], dtype=float)
        r = rsi(s, 14).dropna()
        assert r.iloc[-1] < 50  # downtrend should give RSI < 50


class TestATR:
    def test_atr_positive(self):
        df = _make_df(30)
        result = atr(df, 14).dropna()
        assert (result > 0).all()


class TestBollingerBands:
    def test_bb_upper_above_lower(self):
        df = _make_df(50)
        upper, mid, lower = bollinger_bands(df["close"], 20, 2.0)
        valid = upper.dropna()
        assert (upper.dropna() > lower.dropna()).all()

    def test_bb_mid_equals_sma(self):
        df = _make_df(50)
        _, mid, _ = bollinger_bands(df["close"], 20, 2.0)
        s = sma(df["close"], 20)
        pd.testing.assert_series_equal(mid, s)


class TestVolumeSpike:
    def test_spike_detected(self):
        df = _make_df(30)
        # Inject a spike in last bar
        df.iloc[-1, df.columns.get_loc("volume")] = df["volume"].mean() * 5
        result = volume_spike(df, lookback=20, multiplier=1.5)
        assert result.iloc[-1] is True or result.iloc[-1] == True

    def test_no_spike_normal(self):
        df = _make_df(30, seed=1)
        result = volume_spike(df, lookback=20, multiplier=3.0)
        # Should have at most a few spikes for random normal data
        assert result.sum() <= 5


class TestEnrich:
    def test_enrich_adds_columns(self):
        df = _make_df(210)  # need 200+ for ema200
        enriched = enrich(df)
        for col in ["ema9", "ema21", "ema50", "ema200", "vwap", "rsi14", "atr14",
                    "bb_upper", "bb_mid", "bb_lower", "vol_spike"]:
            assert col in enriched.columns, f"Missing column: {col}"


class TestPrevDayLevels:
    def test_prev_day_levels(self):
        df = _make_df(5)
        df.iloc[-2, df.columns.get_loc("high")] = 23000.0
        df.iloc[-2, df.columns.get_loc("low")] = 21000.0
        h, l = prev_day_levels(df)
        assert h == 23000.0
        assert l == 21000.0

    def test_insufficient_data(self):
        df = _make_df(1)
        h, l = prev_day_levels(df)
        assert h != h  # NaN
