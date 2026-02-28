"""
execution/position_manager.py
Tracks open positions, monitors SL/target hits, handles auto-exit at 3:20 PM.
Trailing SL: once price hits 50% of target gain, trail SL up by TRAIL_SL_PCT.
"""
from datetime import datetime
from typing import Optional

from broker.angel_client import AngelClient
from storage.database import DatabaseManager
from storage.models import Trade
from config import settings
from utils.logger import get_logger
from utils import telegram as tg

logger = get_logger(__name__)


class PositionManager:
    """
    Monitors all open trades every tick/minute.
    Exits positions when SL/target is hit or at 3:20 PM.
    """

    def __init__(self, client: AngelClient) -> None:
        self.client = client
        # Trade id -> trailing SL price (updated dynamically)
        self._trailing_sl: dict[int, float] = {}
        # Trade id -> whether trailing has been activated
        self._trailing_active: dict[int, bool] = {}

    # ── Main monitoring loop (called every minute) ────────────────────────────

    def monitor_positions(self, force_exit: bool = False) -> None:
        """
        Check all open positions for SL hit, target hit, or force exit.
        
        Args:
            force_exit: If True, exit all open positions immediately (3:20 PM call)
        """
        open_trades = DatabaseManager.get_open_trades()

        if not open_trades:
            return

        logger.info(f"👀 Monitoring {len(open_trades)} open position(s)")

        for trade in open_trades:
            try:
                if force_exit:
                    self._exit_trade(trade, reason="MANUAL_EXIT", notes="Auto-exit at 3:20 PM")
                    continue

                current_ltp = self._get_option_ltp(trade)
                if current_ltp is None:
                    continue

                self._check_and_update(trade, current_ltp)

            except Exception as exc:
                logger.error(f"Position monitor error for trade {trade.id}: {exc}", exc_info=True)

    def _check_and_update(self, trade: Trade, current_ltp: float) -> None:
        """Evaluate SL / target / trailing SL for a single trade."""
        trade_id = trade.id
        sl_price = self._trailing_sl.get(trade_id, trade.sl_price)
        target_price = trade.target_price or float("inf")

        unrealized_pnl = (current_ltp - trade.entry_price) * trade.quantity * trade.lot_size
        pct_move = ((current_ltp - trade.entry_price) / trade.entry_price) * 100

        logger.debug(
            f"Trade {trade_id} | {trade.symbol} | LTP={current_ltp:.2f} "
            f"SL={sl_price:.2f} target={target_price:.2f} "
            f"PnL=₹{unrealized_pnl:.2f} ({pct_move:.1f}%)"
        )

        # ── SL hit ─────────────────────────────────────────────────────────
        if current_ltp <= sl_price:
            logger.warning(
                f"⛔ SL HIT | Trade {trade_id} | {trade.symbol} "
                f"LTP={current_ltp:.2f} ≤ SL={sl_price:.2f}"
            )
            self._exit_trade(trade, reason="SL_HIT", exit_price=current_ltp)
            return

        # ── Target hit ──────────────────────────────────────────────────────
        if current_ltp >= target_price:
            logger.info(
                f"🎯 TARGET HIT | Trade {trade_id} | {trade.symbol} "
                f"LTP={current_ltp:.2f} ≥ target={target_price:.2f}"
            )
            self._exit_trade(trade, reason="TARGET_HIT", exit_price=current_ltp)
            return

        # ── Trailing SL activation (beyond 50% of gain target) ─────────────
        half_target_gain = (target_price - trade.entry_price) * 0.5
        if (
            not self._trailing_active.get(trade_id, False)
            and current_ltp >= trade.entry_price + half_target_gain
        ):
            new_trail_sl = round(current_ltp * (1 - settings.TRAIL_SL_PCT / 100), 2)
            if new_trail_sl > (trade.sl_price or 0):
                self._trailing_sl[trade_id] = new_trail_sl
                self._trailing_active[trade_id] = True
                logger.info(
                    f"📈 Trailing SL activated | Trade {trade_id} | "
                    f"new SL={new_trail_sl:.2f} (was {trade.sl_price:.2f})"
                )

        # ── Update trailing SL upward if price moves further ────────────────
        elif self._trailing_active.get(trade_id, False):
            candidate_sl = round(current_ltp * (1 - settings.TRAIL_SL_PCT / 100), 2)
            current_trail = self._trailing_sl.get(trade_id, trade.sl_price or 0)
            if candidate_sl > current_trail:
                self._trailing_sl[trade_id] = candidate_sl
                logger.debug(f"Trail SL updated for trade {trade_id}: {current_trail:.2f} → {candidate_sl:.2f}")

    # ── Exit execution ────────────────────────────────────────────────────────

    def _exit_trade(
        self,
        trade: Trade,
        reason: str,
        exit_price: Optional[float] = None,
        notes: str = "",
    ) -> None:
        """Place exit order and update DB."""
        # Get current price if not provided
        if exit_price is None:
            exit_price = self._get_option_ltp(trade) or trade.entry_price

        # Place SELL order
        from execution.order_manager import OrderManager
        order_id = self.client.place_order({
            "variety": "NORMAL",
            "tradingsymbol": trade.symbol,
            "symboltoken": trade.entry_order_id or "",  # use resolved token
            "transactiontype": "SELL",
            "exchange": settings.NFO,
            "ordertype": "MARKET",
            "producttype": "CARRYFORWARD",
            "duration": "DAY",
            "price": "0",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(trade.quantity * trade.lot_size),
        }) if not settings.PAPER_TRADING else f"PAPER_EXIT_{trade.id}"

        # Calculate and persist PnL
        pnl = DatabaseManager.close_trade(
            trade_id=trade.id,
            exit_price=exit_price,
            exit_order_id=str(order_id),
            status=reason,
            notes=notes,
        )

        # Cleanup trailing SL tracking
        self._trailing_sl.pop(trade.id, None)
        self._trailing_active.pop(trade.id, None)

        # Alert
        if pnl is not None:
            emoji = "✅" if pnl >= 0 else "❌"
            tg.send_message(
                f"{emoji} <b>{reason}</b>\n"
                f"Symbol : {trade.symbol}\n"
                f"Strategy: {trade.strategy}\n"
                f"Entry  : ₹{trade.entry_price:.2f}\n"
                f"Exit   : ₹{exit_price:.2f}\n"
                f"PnL    : ₹{pnl:.2f}"
            )

    def exit_all_positions(self) -> None:
        """Force exit all open positions. Called at 3:20 PM."""
        logger.warning("🔔 3:20 PM — Exiting all open positions")
        self.monitor_positions(force_exit=True)

    def get_unrealized_pnl(self) -> float:
        """Sum unrealized PnL across all open trades."""
        open_trades = DatabaseManager.get_open_trades()
        total = 0.0
        for trade in open_trades:
            ltp = self._get_option_ltp(trade)
            if ltp:
                pnl = (ltp - trade.entry_price) * trade.quantity * trade.lot_size
                total += pnl
        return round(total, 2)

    def _get_option_ltp(self, trade: Trade) -> Optional[float]:
        """Fetch current LTP for an option trade."""
        try:
            return self.client.get_ltp(settings.NFO, trade.symbol, trade.entry_order_id or "")
        except Exception as exc:
            logger.warning(f"LTP fetch failed for {trade.symbol}: {exc}")
            return None
