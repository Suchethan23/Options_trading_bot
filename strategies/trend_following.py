"""
strategies/trend_following.py
Trend Following Options Buying Strategy.

Rules (Bullish):
  - EMA21 > EMA50 > EMA200 (uptrend)
  - Price pulled back to within 0.3% of EMA21
  - RSI > 55 (momentum confirmed)
  → Buy ATM CE

Rules (Bearish):
  - EMA21 < EMA50 < EMA200 (downtrend)
  - Price bounced up to within 0.3% of EMA21
  - RSI < 45
  → Buy ATM PE
"""
from typing import Optional

from strategies.base_strategy import BaseStrategy, Signal
from config.settings import SL_PCT, TARGET_PCT
from utils.logger import get_logger

logger = get_logger(__name__)

_PULLBACK_THRESHOLD = 0.003   # 0.3% proximity to EMA21
_RSI_BULL_MIN = 55
_RSI_BEAR_MAX = 45


class TrendFollowingStrategy(BaseStrategy):
    name = "trend_following"

    def generate_signal(self, snapshot: dict) -> Optional[Signal]:
        underlying = snapshot.get("symbol", "")
        ltp = snapshot.get("ltp")
        df = snapshot.get("df_5min")

        if ltp is None or df is None or df.empty:
            return None

        # Need enough bars for EMA200
        if len(df) < 200:
            logger.debug(f"{self.name}: not enough bars ({len(df)}), need 200")
            return None

        ema21 = self._safe_last(df, "ema21")
        ema50 = self._safe_last(df, "ema50")
        ema200 = self._safe_last(df, "ema200")
        rsi = self._safe_last(df, "rsi14")

        if any(v != v for v in [ema21, ema50, ema200, rsi]):  # NaN check
            return None

        # ── Bullish: uptrend + pullback to EMA21 + RSI momentum ─────────────
        if ema21 > ema50 > ema200:
            ema21_deviation = abs(ltp - ema21) / ema21
            if ema21_deviation <= _PULLBACK_THRESHOLD and rsi > _RSI_BULL_MIN:
                confidence = self._compute_confidence(rsi, ema21, ema50, ema200, "bull")
                logger.info(
                    f"📈 {self.name} BULLISH signal | {underlying} | "
                    f"RSI={rsi:.1f} EMA21={ema21:.2f} dev={ema21_deviation*100:.2f}% "
                    f"conf={confidence:.2f}"
                )
                return Signal(
                    underlying=underlying,
                    option_type="CE",
                    strategy_name=self.name,
                    confidence=confidence,
                    sl_pct=SL_PCT,
                    target_pct=TARGET_PCT,
                    reason=(
                        f"EMA21({ema21:.0f})>EMA50({ema50:.0f})>EMA200({ema200:.0f}), "
                        f"Price pullback to EMA21 ({ema21_deviation*100:.2f}%), RSI={rsi:.1f}"
                    ),
                )

        # ── Bearish: downtrend + bounce to EMA21 + RSI weak ─────────────────
        if ema21 < ema50 < ema200:
            ema21_deviation = abs(ltp - ema21) / ema21
            if ema21_deviation <= _PULLBACK_THRESHOLD and rsi < _RSI_BEAR_MAX:
                confidence = self._compute_confidence(rsi, ema21, ema50, ema200, "bear")
                logger.info(
                    f"📉 {self.name} BEARISH signal | {underlying} | "
                    f"RSI={rsi:.1f} EMA21={ema21:.2f} dev={ema21_deviation*100:.2f}% "
                    f"conf={confidence:.2f}"
                )
                return Signal(
                    underlying=underlying,
                    option_type="PE",
                    strategy_name=self.name,
                    confidence=confidence,
                    sl_pct=SL_PCT,
                    target_pct=TARGET_PCT,
                    reason=(
                        f"EMA21({ema21:.0f})<EMA50({ema50:.0f})<EMA200({ema200:.0f}), "
                        f"Price bounce to EMA21 ({ema21_deviation*100:.2f}%), RSI={rsi:.1f}"
                    ),
                )

        return None

    @staticmethod
    def _compute_confidence(
        rsi: float,
        ema21: float,
        ema50: float,
        ema200: float,
        direction: str,
    ) -> float:
        """
        Score from 0.60 to 0.90.
        - EMA stack spread: wider = stronger trend
        - RSI: further from 50 = stronger momentum
        """
        if direction == "bull":
            ema_spread = (ema21 - ema200) / ema200  # positive
            rsi_score = min((rsi - 50) / 50, 1.0)   # 0..1
        else:
            ema_spread = (ema200 - ema21) / ema200   # positive
            rsi_score = min((50 - rsi) / 50, 1.0)

        ema_score = min(ema_spread * 5, 1.0)  # scale
        confidence = 0.60 + 0.30 * (0.6 * rsi_score + 0.4 * ema_score)
        return round(min(max(confidence, 0.60), 0.90), 2)
