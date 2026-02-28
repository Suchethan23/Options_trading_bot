"""
data/market_data.py
MarketDataEngine: fetches OHLCV candles and computes all technical indicators.
Caches data within the same minute to avoid redundant API calls.
"""
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from broker.angel_client import AngelClient
from config import settings
from utils.indicators import enrich, prev_day_levels
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Cache helpers ─────────────────────────────────────────────────────────────

_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}  # key -> (timestamp, df)
_CACHE_TTL_SECONDS = 55  # refresh just before new minute


def _cache_key(symbol: str, interval: str) -> str:
    return f"{symbol}:{interval}"


def _is_expired(ts: float) -> bool:
    return (time.monotonic() - ts) > _CACHE_TTL_SECONDS


# ── From/To date helpers ──────────────────────────────────────────────────────

def _date_range(interval: str) -> tuple[str, str]:
    """Returns (from_date, to_date) strings suitable for getCandleData."""
    now = datetime.now()
    fmt = "%Y-%m-%d %H:%M"

    if interval in (settings.INTERVAL_1MIN, settings.INTERVAL_5MIN):
        from_dt = now - timedelta(hours=6)
    elif interval == settings.INTERVAL_15MIN:
        from_dt = now - timedelta(days=5)
    else:  # daily
        from_dt = now - timedelta(days=60)

    return from_dt.strftime(fmt), now.strftime(fmt)


# ── Market Data Engine ────────────────────────────────────────────────────────

class MarketDataEngine:
    """
    Fetches and caches enriched OHLCV data for all configured symbols.
    All data is enriched with EMA, VWAP, RSI, ATR, Bollinger, volume spike.
    """

    def __init__(self, client: AngelClient) -> None:
        self.client = client

    def _fetch_candles(
        self,
        symbol: str,
        token: str,
        interval: str,
        exchange: str = settings.NSE,
    ) -> pd.DataFrame:
        """Fetch raw candles from broker and return as enriched DataFrame."""
        key = _cache_key(symbol, interval)
        if key in _CACHE and not _is_expired(_CACHE[key][0]):
            return _CACHE[key][1].copy()

        from_date, to_date = _date_range(interval)
        try:
            raw = self.client.get_candles(
                token=token,
                symbol=symbol,
                interval=interval,
                from_date=from_date,
                to_date=to_date,
                exchange=exchange,
            )
        except Exception as exc:
            logger.error(f"Failed to fetch candles for {symbol}/{interval}: {exc}")
            # Return cached data if available even if expired
            if key in _CACHE:
                return _CACHE[key][1].copy()
            return pd.DataFrame()

        if not raw:
            logger.warning(f"No candle data for {symbol}/{interval}")
            return pd.DataFrame()

        df = pd.DataFrame(raw)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
        df = df.rename(columns=str.lower)

        # Enrich with indicators
        df = enrich(df)

        _CACHE[key] = (time.monotonic(), df)
        logger.debug(f"Fetched {len(df)} candles for {symbol}/{interval}")
        return df.copy()

    def get_5min(self, symbol: str, token: str) -> pd.DataFrame:
        return self._fetch_candles(symbol, token, settings.INTERVAL_5MIN)

    def get_15min(self, symbol: str, token: str) -> pd.DataFrame:
        return self._fetch_candles(symbol, token, settings.INTERVAL_15MIN)

    def get_daily(self, symbol: str, token: str) -> pd.DataFrame:
        return self._fetch_candles(symbol, token, settings.INTERVAL_1DAY)

    def get_ltp(self, exchange: str, symbol: str, token: str) -> Optional[float]:
        """Fetch LTP with error handling."""
        try:
            return self.client.get_ltp(exchange, symbol, token)
        except Exception as exc:
            logger.error(f"LTP fetch failed for {symbol}: {exc}")
            return None

    def get_prev_day_levels(self, symbol: str, token: str) -> tuple[float, float]:
        """Returns (prev_day_high, prev_day_low)."""
        daily = self.get_daily(symbol, token)
        if daily.empty:
            return float("nan"), float("nan")
        return prev_day_levels(daily)

    def get_market_snapshot(self, symbol: str, token: str) -> dict:
        """
        Returns a comprehensive market snapshot for strategy use.
        
        Returns dict with:
            ltp, df_5min, df_15min, df_daily,
            prev_high, prev_low
        """
        ltp = self.get_ltp(settings.NSE, symbol, token)
        df_5min = self.get_5min(symbol, token)
        df_15min = self.get_15min(symbol, token)
        df_daily = self.get_daily(symbol, token)
        prev_high, prev_low = self.get_prev_day_levels(symbol, token)

        return {
            "symbol": symbol,
            "token": token,
            "ltp": ltp,
            "df_5min": df_5min,
            "df_15min": df_15min,
            "df_daily": df_daily,
            "prev_day_high": prev_high,
            "prev_day_low": prev_low,
            "timestamp": datetime.now(),
        }
