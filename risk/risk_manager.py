"""
risk/risk_manager.py
Risk management: daily loss cap, max open trades, market hours check,
SL/target computation. Critical safety layer before any order goes through.
"""
from datetime import datetime
from typing import Optional

import pytz

from config import settings
from storage.database import DatabaseManager
from strategies.base_strategy import Signal
from utils.logger import get_logger
from utils import telegram as tg

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")


def _ist_now() -> datetime:
    return datetime.now(tz=IST)


class RiskManager:
    """
    All risk checks that must pass before a trade is placed.
    Stateless w.r.t. positions — reads from DB for accuracy.
    """

    def __init__(self) -> None:
        self._trading_halted = False
        self._halt_reason = ""

    # ── Public API ────────────────────────────────────────────────────────────

    def is_trading_allowed(self) -> tuple[bool, str]:
        """
        Master check — call before processing any signal.
        Returns (allowed, reason).
        """
        if self._trading_halted:
            return False, f"Trading halted: {self._halt_reason}"

        # Market hours check
        ok, reason = self._check_market_hours()
        if not ok:
            return False, reason

        # Daily loss cap
        ok, reason = self._check_daily_loss()
        if not ok:
            self._halt_trading(reason)
            return False, reason

        # Max open trades
        ok, reason = self._check_max_trades()
        if not ok:
            return False, reason

        return True, ""

    def can_trade_signal(self, signal: Signal) -> tuple[bool, str]:
        """
        Signal-level checks (in addition to is_trading_allowed).
        Returns (allowed, reason).
        """
        # Check if symbol already traded today
        already_traded = DatabaseManager.is_symbol_traded_today(signal.underlying)
        if already_traded:
            return False, f"{signal.underlying} already traded today (dedup)"

        return True, ""

    def compute_sl_target(self, signal: Signal, option_ltp: float) -> tuple[float, float]:
        """
        Compute absolute SL and target prices from option LTP and signal pct.
        
        For a bought option:
          SL    = entry_price * (1 - sl_pct/100)
          Target = entry_price * (1 + target_pct/100)
        """
        sl_price = round(option_ltp * (1 - signal.sl_pct / 100), 2)
        target_price = round(option_ltp * (1 + signal.target_pct / 100), 2)
        logger.info(
            f"💰 Risk levels for {signal.underlying} {signal.option_type} "
            f"entry={option_ltp:.2f} SL={sl_price:.2f} target={target_price:.2f}"
        )
        return max(sl_price, 0.05), target_price

    def get_today_pnl(self) -> float:
        return DatabaseManager.get_today_realized_pnl()

    # ── Internal checks ───────────────────────────────────────────────────────

    def _check_market_hours(self) -> tuple[bool, str]:
        now = _ist_now()
        market_open = now.replace(
            hour=settings.MARKET_OPEN_HOUR,
            minute=settings.MARKET_OPEN_MINUTE,
            second=0,
            microsecond=0,
        )
        market_close = now.replace(
            hour=settings.MARKET_CLOSE_HOUR,
            minute=settings.MARKET_CLOSE_MINUTE,
            second=0,
            microsecond=0,
        )
        if not (market_open <= now <= market_close):
            return False, f"Outside market hours ({now.strftime('%H:%M')} IST)"
        return True, ""

    def _check_daily_loss(self) -> tuple[bool, str]:
        realized_pnl = DatabaseManager.get_today_realized_pnl()
        max_loss = -(settings.CAPITAL * settings.MAX_DAILY_LOSS_PCT / 100)
        if realized_pnl <= max_loss:
            reason = (
                f"Daily loss cap hit: ₹{realized_pnl:.2f} "
                f"(limit: ₹{max_loss:.2f}, {settings.MAX_DAILY_LOSS_PCT}% of capital)"
            )
            return False, reason
        return True, ""

    def _check_max_trades(self) -> tuple[bool, str]:
        open_count = DatabaseManager.get_open_trade_count()
        if open_count >= settings.MAX_OPEN_TRADES:
            return False, f"Max open trades reached ({open_count}/{settings.MAX_OPEN_TRADES})"
        return True, ""

    def _halt_trading(self, reason: str) -> None:
        if not self._trading_halted:
            self._trading_halted = True
            self._halt_reason = reason
            logger.critical(f"⛔ TRADING HALTED: {reason}")
            tg.alert_risk_halt(reason)

    def reset_daily_halt(self) -> None:
        """Call at start of each trading day to reset daily halt flags."""
        self._trading_halted = False
        self._halt_reason = ""
        logger.info("🔄 Daily risk counters reset")

    def is_auto_exit_time(self) -> bool:
        """Returns True if it's time to auto-exit all positions (3:20 PM IST)."""
        now = _ist_now()
        exit_time = now.replace(
            hour=settings.AUTO_EXIT_HOUR,
            minute=settings.AUTO_EXIT_MINUTE,
            second=0,
            microsecond=0,
        )
        return now >= exit_time
