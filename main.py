"""
main.py
Entry point for the Angel One Options Trading Agent.
Runs a 1-minute scheduled loop during IST market hours (9:15 – 15:30).

Usage:
    python main.py           # Uses .env for paper/live mode
    python main.py --paper   # Force paper trading mode
    python main.py --live    # Force live mode (requires real credentials)
"""
import argparse
import signal
import sys
import os
from datetime import datetime

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler

# ── Bootstrap config before other imports ───────────────────────────────────
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Angel One Options Trading Agent")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--paper", action="store_true", help="Force paper trading mode")
    group.add_argument("--live", action="store_true", help="Force live trading mode")
    return parser.parse_args()

args = _parse_args()
if args.paper:
    os.environ["PAPER_TRADING"] = "True"
elif args.live:
    os.environ["PAPER_TRADING"] = "False"

from config import settings
from config.settings import validate_config
from broker.angel_client import get_client
from data.market_data import MarketDataEngine
from data.option_chain import OptionChainManager
from strategies.strategy_manager import StrategyManager
from execution.order_manager import OrderManager
from execution.position_manager import PositionManager
from risk.risk_manager import RiskManager
from storage.database import DatabaseManager, init_db
from utils.logger import get_logger
from utils import telegram as tg

logger = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ── Global component instances ───────────────────────────────────────────────
client = None
market_data = None
option_chain = None
strategy_manager = None
order_manager = None
position_manager = None
risk_manager = None


def initialize() -> None:
    """Initialize all components and authenticate."""
    global client, market_data, option_chain, strategy_manager
    global order_manager, position_manager, risk_manager

    logger.info("=" * 60)
    logger.info("🚀 Angel One Options Trading Agent Starting")
    mode = "📄 PAPER TRADING" if settings.PAPER_TRADING else "💸 LIVE TRADING"
    logger.info(f"   Mode    : {mode}")
    logger.info(f"   Capital : ₹{settings.CAPITAL:,.0f}")
    logger.info(f"   Symbols : {', '.join(settings.SYMBOLS)}")
    logger.info(f"   Strategies: {', '.join(settings.ENABLED_STRATEGIES)}")
    logger.info("=" * 60)

    # Validate config (raises for missing live credentials)
    validate_config()

    # Init DB
    init_db()

    # Init broker client
    client = get_client()
    client.login()

    # Init data layer
    market_data = MarketDataEngine(client)
    option_chain = OptionChainManager(client)

    # Init strategy / risk / execution
    strategy_manager = StrategyManager()
    risk_manager = RiskManager()
    risk_manager.reset_daily_halt()
    order_manager = OrderManager(client, option_chain)
    position_manager = PositionManager(client)

    tg.send_message(f"🚀 Trading agent started | {mode} | Capital: ₹{settings.CAPITAL:,.0f}")
    logger.info("✅ All components initialized")


# ── Main trading cycle (runs every minute) ───────────────────────────────────

def trading_cycle() -> None:
    """One complete trading cycle — executed every minute."""
    try:
        now = datetime.now(tz=IST)
        logger.info(f"⏱  Cycle start: {now.strftime('%H:%M:%S')} IST")

        # ── Step 1: Monitor open positions first ────────────────────────────
        # Check for auto-exit time (3:20 PM)
        if risk_manager.is_auto_exit_time():
            logger.warning("🔔 Auto-exit time reached (3:20 PM)")
            position_manager.exit_all_positions()
            _log_daily_summary()
            return

        # Regular position monitoring (SL/target checks)
        position_manager.monitor_positions()

        # ── Step 2: Check if new trades are allowed ─────────────────────────
        allowed, reason = risk_manager.is_trading_allowed()
        if not allowed:
            logger.info(f"⏸  No new trades: {reason}")
            return

        # ── Step 3: Fetch market data for all symbols ───────────────────────
        snapshots = []
        for symbol in settings.SYMBOLS:
            token = settings.INDEX_TOKENS.get(symbol.upper(), "")
            if not token:
                logger.warning(f"No token configured for {symbol}")
                continue
            try:
                snapshot = market_data.get_market_snapshot(symbol, token)
                if snapshot.get("ltp"):
                    snapshots.append(snapshot)
                    logger.debug(f"📊 {symbol} LTP={snapshot['ltp']:.2f}")
            except Exception as exc:
                logger.error(f"Snapshot failed for {symbol}: {exc}")

        if not snapshots:
            logger.warning("No valid market data received")
            return

        # ── Step 4: Evaluate strategies ─────────────────────────────────────
        # Get already-open underlying symbols for deduplication
        open_trades = DatabaseManager.get_open_trades()
        already_traded = {t.underlying for t in open_trades}

        signals = strategy_manager.evaluate_all_symbols(snapshots, already_traded)

        if not signals:
            logger.info("📭 No signals generated this cycle")
            return

        # ── Step 5: Process best signals (up to available trade slots) ───────
        open_count = DatabaseManager.get_open_trade_count()
        slots_available = settings.MAX_OPEN_TRADES - open_count

        for signal in signals[:slots_available]:
            # Save signal to DB
            signal_id = DatabaseManager.save_signal({
                "symbol": signal.trading_symbol or signal.underlying,
                "underlying": signal.underlying,
                "strategy": signal.strategy_name,
                "option_type": signal.option_type,
                "strike": signal.strike or 0,
                "action": "BUY",
                "confidence": signal.confidence,
                "sl_pct": signal.sl_pct,
                "target_pct": signal.target_pct,
            })

            # Place trade
            trade_id = order_manager.process_signal(signal)
            if trade_id:
                DatabaseManager.mark_signal_traded(signal_id)
                logger.info(f"🎉 Trade executed: trade_id={trade_id}")
            else:
                DatabaseManager.mark_signal_skipped(signal_id, "order_manager_blocked")

        # ── Step 6: Log cycle summary ────────────────────────────────────────
        unrealized_pnl = position_manager.get_unrealized_pnl()
        realized_pnl = risk_manager.get_today_pnl()
        logger.info(
            f"📈 Cycle done | Realized: ₹{realized_pnl:.2f} "
            f"Unrealized: ₹{unrealized_pnl:.2f} "
            f"Open trades: {DatabaseManager.get_open_trade_count()}"
        )

    except Exception as exc:
        logger.error(f"Trading cycle error: {exc}", exc_info=True)


def _log_daily_summary() -> None:
    """Log and alert end-of-day PnL summary."""
    realized = DatabaseManager.get_today_realized_pnl()
    unrealized = position_manager.get_unrealized_pnl()
    signals = DatabaseManager.get_today_signal_count()

    logger.info("=" * 60)
    logger.info(f"📊 DAILY SUMMARY")
    logger.info(f"   Realized PnL  : ₹{realized:.2f}")
    logger.info(f"   Unrealized PnL: ₹{unrealized:.2f}")
    logger.info(f"   Total PnL     : ₹{realized + unrealized:.2f}")
    logger.info(f"   Signals today : {signals}")
    logger.info("=" * 60)

    DatabaseManager.upsert_daily_pnl({
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "signals_generated": signals,
        "is_paper": settings.PAPER_TRADING,
    })

    tg.alert_pnl(realized, unrealized)


# ── Graceful shutdown ────────────────────────────────────────────────────────

def shutdown(signum, frame) -> None:
    logger.info("🛑 Shutdown signal received — exiting gracefully")
    if position_manager:
        logger.info("Exiting all open positions before shutdown...")
        position_manager.exit_all_positions()
    _log_daily_summary()
    sys.exit(0)


# ── Scheduler ────────────────────────────────────────────────────────────────

def main() -> None:
    initialize()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    scheduler = BlockingScheduler(timezone=IST)

    # Run trading cycle every minute during market hours
    scheduler.add_job(
        trading_cycle,
        trigger="cron",
        day_of_week="mon-fri",
        hour=f"{settings.MARKET_OPEN_HOUR}-{settings.MARKET_CLOSE_HOUR}",
        minute="*",
        id="trading_cycle",
        name="Main Trading Cycle",
        misfire_grace_time=30,
    )

    # Auto-exit job at 3:20 PM
    scheduler.add_job(
        position_manager.exit_all_positions if position_manager else lambda: None,
        trigger="cron",
        day_of_week="mon-fri",
        hour=settings.AUTO_EXIT_HOUR,
        minute=settings.AUTO_EXIT_MINUTE,
        id="auto_exit",
        name="Auto Exit All Positions",
    )

    logger.info(
        f"⏰ Scheduler started. Trading every minute "
        f"Mon–Fri {settings.MARKET_OPEN_HOUR}:{settings.MARKET_OPEN_MINUTE:02d}–"
        f"{settings.MARKET_CLOSE_HOUR}:{settings.MARKET_CLOSE_MINUTE:02d} IST"
    )

    # Run one immediate cycle if currently in market hours
    from risk.risk_manager import _ist_now
    now = _ist_now()
    market_open_check = now.replace(
        hour=settings.MARKET_OPEN_HOUR,
        minute=settings.MARKET_OPEN_MINUTE,
        second=0, microsecond=0
    )
    market_close_check = now.replace(
        hour=settings.MARKET_CLOSE_HOUR,
        minute=settings.MARKET_CLOSE_MINUTE,
        second=0, microsecond=0
    )
    if market_open_check <= now <= market_close_check and now.weekday() < 5:
        logger.info("🏃 Running immediate cycle (market is open)")
        trading_cycle()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
