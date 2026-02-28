"""
utils/indicators.py
Pure pandas/numpy technical indicator functions used by data engine and strategies.
All functions operate on pandas Series or DataFrames and return Series/scalar values.
"""
import numpy as np
import pandas as pd


# ── Moving Averages ──────────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period).mean()


# ── VWAP ────────────────────────────────────────────────────────────────────

def vwap(df: pd.DataFrame) -> pd.Series:
    """
    Volume Weighted Average Price (intraday reset each day).
    Requires columns: high, low, close, volume.
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df.groupby(df.index.date)["volume"].transform("cumsum")  # type: ignore[union-attr]
    cum_tp_vol = df.groupby(df.index.date).apply(
        lambda g: (typical_price.loc[g.index] * g["volume"]).cumsum()
    ).reset_index(level=0, drop=True)
    return cum_tp_vol / cum_vol


def vwap_simple(df: pd.DataFrame) -> pd.Series:
    """
    Simplified VWAP — cumulative over the entire series (no daily reset).
    Use this when DataFrame already contains only today's intraday data.
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical_price * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


# ── RSI ──────────────────────────────────────────────────────────────────────

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


# ── ATR ──────────────────────────────────────────────────────────────────────

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


# ── Bollinger Bands ──────────────────────────────────────────────────────────

def bollinger_bands(
    series: pd.Series, period: int = 20, std_dev: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Returns (upper, middle, lower) Bollinger Bands.
    """
    middle = sma(series, period)
    std = series.rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


# ── Volume ───────────────────────────────────────────────────────────────────

def volume_spike(df: pd.DataFrame, lookback: int = 20, multiplier: float = 1.5) -> pd.Series:
    """
    Returns a boolean Series: True where current volume > multiplier × avg_volume (lookback).
    """
    avg_vol = df["volume"].rolling(window=lookback).mean()
    return df["volume"] > (multiplier * avg_vol)


# ── Previous Day High/Low ─────────────────────────────────────────────────────

def prev_day_levels(daily_df: pd.DataFrame) -> tuple[float, float]:
    """
    Given a daily OHLCV DataFrame, returns the (high, low) of the most recent completed day.
    daily_df must have at least 2 rows (today + previous).
    """
    if len(daily_df) < 2:
        return float("nan"), float("nan")
    prev = daily_df.iloc[-2]
    return float(prev["high"]), float(prev["low"])


# ── EMA Alignment Signal ─────────────────────────────────────────────────────

def ema_alignment(
    close: pd.Series,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Returns (ema9, ema21, ema50, ema200)."""
    return ema(close, 9), ema(close, 21), ema(close, 50), ema(close, 200)


# ── VWAP Deviation ───────────────────────────────────────────────────────────

def vwap_deviation_pct(close: float, vwap_val: float) -> float:
    """Returns % deviation of close from VWAP. Positive = above VWAP."""
    if vwap_val == 0:
        return 0.0
    return ((close - vwap_val) / vwap_val) * 100


# ── Convenience: Enrich DataFrame with All Indicators ────────────────────────

def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds all indicators to a copy of df.
    Required columns: open, high, low, close, volume.
    Returns enriched DataFrame.
    """
    df = df.copy()

    close = df["close"]
    df["ema9"] = ema(close, 9)
    df["ema21"] = ema(close, 21)
    df["ema50"] = ema(close, 50)
    df["ema200"] = ema(close, 200)
    df["vwap"] = vwap_simple(df)
    df["rsi14"] = rsi(close, 14)
    df["atr14"] = atr(df, 14)

    bb_upper, bb_mid, bb_lower = bollinger_bands(close, 20, 2.0)
    df["bb_upper"] = bb_upper
    df["bb_mid"] = bb_mid
    df["bb_lower"] = bb_lower

    df["vol_spike"] = volume_spike(df, 20, 1.5)

    return df
