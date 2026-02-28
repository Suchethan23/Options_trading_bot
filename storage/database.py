"""
storage/database.py
Database session management and CRUD helpers using SQLAlchemy.
Supports SQLite (default) and PostgreSQL via DATABASE_URL env var.
"""
from contextlib import contextmanager
from datetime import date, datetime
from typing import Generator, Optional

from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker

from storage.models import Base, Trade, Signal, DailyPnL
from config.settings import DATABASE_URL
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Engine & Session factory ─────────────────────────────────────────────────

_engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    echo=False,
)
SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


def init_db() -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=_engine)
    logger.info(f"✅ Database initialized: {DATABASE_URL}")


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Context manager that yields a Session and handles commit/rollback."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── Trade helpers ─────────────────────────────────────────────────────────────

class DatabaseManager:
    """High-level DB operations used by the trading agent."""

    # ── Signals ───────────────────────────────────────────────────────────────

    @staticmethod
    def save_signal(signal_data: dict) -> int:
        """Persist a signal and return its ID."""
        with get_session() as session:
            signal = Signal(**signal_data)
            session.add(signal)
            session.flush()
            signal_id = signal.id
        return signal_id

    @staticmethod
    def mark_signal_traded(signal_id: int) -> None:
        with get_session() as session:
            sig = session.get(Signal, signal_id)
            if sig:
                sig.was_traded = True

    @staticmethod
    def mark_signal_skipped(signal_id: int, reason: str) -> None:
        with get_session() as session:
            sig = session.get(Signal, signal_id)
            if sig:
                sig.skip_reason = reason

    # ── Trades ────────────────────────────────────────────────────────────────

    @staticmethod
    def create_trade(trade_data: dict) -> int:
        """Insert a new trade and return its ID."""
        with get_session() as session:
            trade = Trade(**trade_data)
            session.add(trade)
            session.flush()
            trade_id = trade.id
        logger.info(f"💾 Trade saved: id={trade_id} symbol={trade_data.get('symbol')}")
        return trade_id

    @staticmethod
    def close_trade(
        trade_id: int,
        exit_price: float,
        exit_order_id: str,
        status: str,
        notes: str = "",
    ) -> Optional[float]:
        """Close a trade and compute PnL. Returns realized PnL."""
        with get_session() as session:
            trade = session.get(Trade, trade_id)
            if not trade:
                logger.warning(f"Trade {trade_id} not found for closing")
                return None
            trade.exit_price = exit_price
            trade.exit_order_id = exit_order_id
            trade.exit_time = datetime.utcnow()
            trade.status = status
            trade.notes = notes

            # PnL calculation (for BUY options: exit - entry)
            premium_paid = trade.entry_price * trade.quantity * trade.lot_size
            premium_received = exit_price * trade.quantity * trade.lot_size
            pnl = premium_received - premium_paid
            trade.pnl = round(pnl, 2)
            trade.pnl_pct = round((pnl / premium_paid) * 100, 2) if premium_paid else 0

            logger.info(
                f"💾 Trade closed: id={trade_id} status={status} "
                f"pnl=₹{pnl:.2f} ({trade.pnl_pct:.1f}%)"
            )
            return pnl

    @staticmethod
    def get_open_trades() -> list[Trade]:
        """Return all currently open trades."""
        with get_session() as session:
            trades = session.query(Trade).filter(Trade.status == "OPEN").all()
            session.expunge_all()
            return trades

    @staticmethod
    def get_open_trade_count() -> int:
        with get_session() as session:
            return session.query(func.count(Trade.id)).filter(Trade.status == "OPEN").scalar() or 0

    @staticmethod
    def is_symbol_traded_today(symbol: str) -> bool:
        """Check if this symbol was already traded today (deduplication)."""
        today = date.today()
        with get_session() as session:
            count = (
                session.query(func.count(Trade.id))
                .filter(
                    Trade.symbol == symbol,
                    func.date(Trade.entry_time) == today,
                )
                .scalar()
                or 0
            )
            return count > 0

    # ── Daily PnL ─────────────────────────────────────────────────────────────

    @staticmethod
    def get_today_realized_pnl() -> float:
        """Sum realized PnL of all closed trades today."""
        today = date.today()
        with get_session() as session:
            total = (
                session.query(func.sum(Trade.pnl))
                .filter(
                    Trade.status.in_(["CLOSED", "SL_HIT", "TARGET_HIT", "MANUAL_EXIT", "EXPIRED"]),
                    func.date(Trade.exit_time) == today,
                )
                .scalar()
                or 0.0
            )
            return float(total)

    @staticmethod
    def upsert_daily_pnl(data: dict) -> None:
        """Insert or update today's daily PnL record."""
        today = date.today()
        with get_session() as session:
            record = session.query(DailyPnL).filter(DailyPnL.date == today).first()
            if record:
                for key, value in data.items():
                    setattr(record, key, value)
            else:
                record = DailyPnL(date=today, **data)
                session.add(record)

    @staticmethod
    def get_today_signal_count() -> int:
        today = date.today()
        with get_session() as session:
            return (
                session.query(func.count(Signal.id))
                .filter(func.date(Signal.generated_at) == today)
                .scalar()
                or 0
            )
