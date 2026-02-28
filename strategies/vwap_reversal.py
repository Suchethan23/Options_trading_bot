"""
strategies/vwap_reversal.py
VWAP Reversion Options Buying Strategy.

Rules (Bullish reversion):
  - Price deviated below VWAP by > 0.5%
  - Price has now crossed back above VWAP (reverting)
  - RSI is rising (current RSI > prev bar RSI)
  → Buy ATM CE

Rules (Bearish reversion):
  - Price deviated above VWAP by > 0.5%
  - Price has now crossed back below VWAP
  - RSI is falling
  → Buy ATM PE
"""
from typing import Optional

from strategies.base_strategy import BaseStrategy, Signal
from config.settings import SL_PCT, TARGET_PCT
from utils.logger import get_logger

logger = get_logger(__name__)

_MIN_DEVIATION_PCT = 0.5   # Minimum VWAP deviation to qualify


class VWAPReversionStrategy(BaseStrategy):
    name = "vwap_reversal"

    def generate_signal(self, snapshot: dict) -> Optional[Signal]:
        underlying = snapshot.get("symbol", "")
        ltp = snapshot.get("ltp")
        df = snapshot.get("df_5min")

        if ltp is None or df is None or df.empty or len(df) < 3:
            return None

        vwap_val = self._safe_last(df, "vwap")
        rsi_now = self._safe_last(df, "rsi14")

        if vwap_val != vwap_val or vwap_val == 0:  # NaN / zero check
            return None

        # Previous bar
        prev_close = float(df["close"].iloc[-2])
        prev_vwap = float(df["vwap"].iloc[-2]) if "vwap" in df.columns else vwap_val
        prev_rsi = float(df["rsi14"].iloc[-2]) if "rsi14" in df.columns else rsi_now

        current_close = float(df["close"].iloc[-1])
        deviation_pct = ((prev_close - prev_vwap) / prev_vwap) * 100

        # ── Bullish reversion: was below VWAP, now crossed above ─────────────
        if (
            deviation_pct < -_MIN_DEVIATION_PCT   # was significantly below VWAP
            and current_close > vwap_val           # now above
            and rsi_now > prev_rsi                 # RSI rising
        ):
            confidence = self._compute_confidence(abs(deviation_pct), rsi_now, prev_rsi)
            logger.info(
                f"🔼 {self.name} BULL REVERSION | {underlying} | "
                f"prev_dev={deviation_pct:.2f}% crossed above VWAP={vwap_val:.2f} "
                f"RSI {prev_rsi:.1f}→{rsi_now:.1f} conf={confidence:.2f}"
            )
            return Signal(
                underlying=underlying,
                option_type="CE",
                strategy_name=self.name,
                confidence=confidence,
                sl_pct=SL_PCT,
                target_pct=TARGET_PCT,
                reason=(
                    f"Price recovered from {deviation_pct:.2f}% below VWAP ({vwap_val:.2f}), "
                    f"RSI rising {prev_rsi:.1f}→{rsi_now:.1f}"
                ),
            )

        # ── Bearish reversion: was above VWAP, now crossed below ─────────────
        if (
            deviation_pct > _MIN_DEVIATION_PCT    # was significantly above VWAP
            and current_close < vwap_val           # now below
            and rsi_now < prev_rsi                 # RSI falling
        ):
            confidence = self._compute_confidence(abs(deviation_pct), prev_rsi, rsi_now)
            logger.info(
                f"🔽 {self.name} BEAR REVERSION | {underlying} | "
                f"prev_dev={deviation_pct:.2f}% crossed below VWAP={vwap_val:.2f} "
                f"RSI {prev_rsi:.1f}→{rsi_now:.1f} conf={confidence:.2f}"
            )
            return Signal(
                underlying=underlying,
                option_type="PE",
                strategy_name=self.name,
                confidence=confidence,
                sl_pct=SL_PCT,
                target_pct=TARGET_PCT,
                reason=(
                    f"Price fell from {deviation_pct:.2f}% above VWAP ({vwap_val:.2f}), "
                    f"RSI falling {prev_rsi:.1f}→{rsi_now:.1f}"
                ),
            )

        return None

    @staticmethod
    def _compute_confidence(deviation_pct: float, rsi_hi: float, rsi_lo: float) -> float:
        """
        Larger deviation + bigger RSI shift → higher confidence.
        Range: 0.65–0.85
        """
        dev_score = min((deviation_pct - 0.5) / 2.0, 1.0)  # 0.5%→0, 2.5%→1
        rsi_shift = min((rsi_hi - rsi_lo) / 20.0, 1.0)
        confidence = 0.65 + 0.20 * (0.5 * dev_score + 0.5 * rsi_shift)
        return round(min(max(confidence, 0.65), 0.85), 2)
