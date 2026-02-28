"""
strategies/breakout.py
Breakout Options Buying Strategy.

Rules (Bullish Breakout):
  - Current price > Previous day's high
  - Volume spike detected (current > 1.5x 20-bar average)
  → Buy ATM CE

Rules (Bearish Breakdown):
  - Current price < Previous day's low
  - Volume spike detected
  → Buy ATM PE
"""
from typing import Optional
import math

from strategies.base_strategy import BaseStrategy, Signal
from config.settings import SL_PCT, TARGET_PCT
from utils.logger import get_logger

logger = get_logger(__name__)

# Buffer to prevent noise — price must exceed level by at least this fraction
_BREAKOUT_BUFFER = 0.001   # 0.1% beyond PDH/PDL


class BreakoutStrategy(BaseStrategy):
    name = "breakout"

    def generate_signal(self, snapshot: dict) -> Optional[Signal]:
        underlying = snapshot.get("symbol", "")
        ltp = snapshot.get("ltp")
        df = snapshot.get("df_5min")
        prev_high = snapshot.get("prev_day_high", float("nan"))
        prev_low = snapshot.get("prev_day_low", float("nan"))

        if ltp is None or df is None or df.empty:
            return None

        if math.isnan(prev_high) or math.isnan(prev_low):
            logger.debug(f"{self.name}: prev day levels not available")
            return None

        if len(df) < 20:
            logger.debug(f"{self.name}: not enough bars for volume analysis")
            return None

        vol_spike = self._safe_last(df, "vol_spike")
        current_vol = self._safe_last(df, "volume", 0)
        avg_vol = float(df["volume"].tail(20).mean())
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0

        # ── Bullish Breakout ──────────────────────────────────────────────────
        breakout_level = prev_high * (1 + _BREAKOUT_BUFFER)
        if ltp > breakout_level and vol_spike:
            confidence = self._compute_confidence(vol_ratio)
            pct_breakout = ((ltp - prev_high) / prev_high) * 100
            logger.info(
                f"🚀 {self.name} BREAKOUT | {underlying} | LTP={ltp:.2f} "
                f"PDH={prev_high:.2f} (+{pct_breakout:.2f}%) vol_ratio={vol_ratio:.1f}x "
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
                    f"Price ({ltp:.2f}) broke PDH ({prev_high:.2f}) by {pct_breakout:.2f}% "
                    f"with {vol_ratio:.1f}x volume spike"
                ),
            )

        # ── Bearish Breakdown ─────────────────────────────────────────────────
        breakdown_level = prev_low * (1 - _BREAKOUT_BUFFER)
        if ltp < breakdown_level and vol_spike:
            confidence = self._compute_confidence(vol_ratio)
            pct_breakdown = ((prev_low - ltp) / prev_low) * 100
            logger.info(
                f"💥 {self.name} BREAKDOWN | {underlying} | LTP={ltp:.2f} "
                f"PDL={prev_low:.2f} (-{pct_breakdown:.2f}%) vol_ratio={vol_ratio:.1f}x "
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
                    f"Price ({ltp:.2f}) broke PDL ({prev_low:.2f}) by {pct_breakdown:.2f}% "
                    f"with {vol_ratio:.1f}x volume spike"
                ),
            )

        return None

    @staticmethod
    def _compute_confidence(vol_ratio: float) -> float:
        """
        Higher volume ratio → higher confidence (cap at 0.88).
        vol_ratio 1.5x → 0.65, 3x → 0.80, 5x+ → 0.88
        """
        score = min((vol_ratio - 1.5) / 5.0, 1.0)
        return round(max(0.65, 0.65 + 0.23 * score), 2)
