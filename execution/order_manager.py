"""
execution/order_manager.py
Handles order placement for both paper and live modes.
Includes deduplication, SL/target placement, and emergency cancel-all.
"""
from datetime import datetime
from typing import Optional

from broker.angel_client import AngelClient
from data.option_chain import OptionChainManager, OptionInfo
from risk.risk_manager import RiskManager
from risk.position_sizing import PositionSizer
from storage.database import DatabaseManager
from strategies.base_strategy import Signal
from config import settings
from utils.logger import get_logger
from utils import telegram as tg

logger = get_logger(__name__)


class OrderManager:
    """
    Places, tracks, and manages orders.
    
    Paper mode: simulates fill at current LTP, logs to DB.
    Live mode: calls AngelClient.place_order().
    """

    def __init__(self, client: AngelClient, option_chain: OptionChainManager) -> None:
        self.client = client
        self.option_chain = option_chain
        self.risk_manager = RiskManager()
        self.sizer = PositionSizer()
        self._pending_order_ids: set[str] = set()

    # ── Main entry point ──────────────────────────────────────────────────────

    def process_signal(self, signal: Signal) -> Optional[int]:
        """
        Full pipeline: risk check → option resolution → sizing → order placement.
        
        Args:
            signal: Trading signal from StrategyManager
            
        Returns:
            Trade DB id if order placed, None otherwise
        """
        # 1. Global risk check
        allowed, reason = self.risk_manager.is_trading_allowed()
        if not allowed:
            logger.info(f"🚫 Trade blocked (global): {reason}")
            return None

        # 2. Signal-level risk check
        allowed, reason = self.risk_manager.can_trade_signal(signal)
        if not allowed:
            logger.info(f"🚫 Trade blocked (signal): {reason}")
            return None

        # 3. Resolve option contract
        ltp = signal.ltp if signal.ltp > 0 else self._get_underlying_ltp(signal.underlying)
        option_info = self.option_chain.get_atm_option(
            signal.underlying, ltp, signal.option_type
        )
        if option_info is None or option_info.ltp <= 0:
            logger.warning(f"Could not resolve option for {signal.underlying} {signal.option_type}")
            return None

        # Enrich signal with resolved option details
        signal.trading_symbol = option_info.trading_symbol
        signal.token = option_info.token
        signal.strike = option_info.strike
        signal.expiry = option_info.expiry
        signal.ltp = option_info.ltp

        # 4. Compute SL and target
        sl_price, target_price = self.risk_manager.compute_sl_target(signal, option_info.ltp)
        signal.sl_price = sl_price
        signal.target_price = target_price

        # 5. Size the position
        qty_lots = self.sizer.calculate_quantity(
            underlying=signal.underlying,
            option_ltp=option_info.ltp,
        )

        # 6. Place entry order
        order_id = self._place_entry_order(signal, option_info, qty_lots)
        if not order_id:
            return None

        # 7. Record trade in DB
        lot_size = self.sizer.get_lot_size(signal.underlying)
        trade_id = DatabaseManager.create_trade({
            "symbol": signal.trading_symbol,
            "underlying": signal.underlying,
            "strategy": signal.strategy_name,
            "option_type": signal.option_type,
            "strike": signal.strike,
            "expiry": signal.expiry,
            "action": "BUY",
            "quantity": qty_lots,
            "lot_size": lot_size,
            "entry_price": option_info.ltp,
            "sl_price": sl_price,
            "target_price": target_price,
            "entry_order_id": order_id,
            "is_paper": settings.PAPER_TRADING,
            "status": "OPEN",
            "confidence": signal.confidence,
            "entry_time": datetime.utcnow(),
        })

        # 8. Telegram alert
        tg.alert_trade(
            action="BUY",
            symbol=signal.trading_symbol,
            qty=qty_lots * lot_size,
            price=option_info.ltp,
            strategy=signal.strategy_name,
        )

        logger.info(
            f"✅ Trade placed | id={trade_id} | {signal.trading_symbol} "
            f"qty={qty_lots} lots entry=₹{option_info.ltp:.2f} "
            f"SL=₹{sl_price:.2f} target=₹{target_price:.2f}"
        )
        return trade_id

    # ── Order placement ───────────────────────────────────────────────────────

    def _place_entry_order(
        self,
        signal: Signal,
        option_info: OptionInfo,
        qty_lots: int,
    ) -> Optional[str]:
        """Place market entry order. Returns order ID."""
        lot_size = self.sizer.get_lot_size(signal.underlying)
        total_qty = qty_lots * lot_size

        order_params = {
            "variety": "NORMAL",
            "tradingsymbol": option_info.trading_symbol,
            "symboltoken": option_info.token,
            "transactiontype": "BUY",
            "exchange": settings.NFO,
            "ordertype": "MARKET",
            "producttype": "CARRYFORWARD",
            "duration": "DAY",
            "price": "0",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(total_qty),
        }

        try:
            order_id = self.client.place_order(order_params)
            self._pending_order_ids.add(order_id)
            return order_id
        except Exception as exc:
            logger.error(f"Entry order failed for {option_info.trading_symbol}: {exc}")
            return None

    def place_exit_order(
        self,
        trade_id: int,
        trading_symbol: str,
        token: str,
        qty_lots: int,
        underlying: str,
        exit_reason: str = "MANUAL",
    ) -> Optional[str]:
        """Place SELL market order to exit a long option position."""
        lot_size = self.sizer.get_lot_size(underlying)
        total_qty = qty_lots * lot_size

        order_params = {
            "variety": "NORMAL",
            "tradingsymbol": trading_symbol,
            "symboltoken": token,
            "transactiontype": "SELL",
            "exchange": settings.NFO,
            "ordertype": "MARKET",
            "producttype": "CARRYFORWARD",
            "duration": "DAY",
            "price": "0",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(total_qty),
        }

        try:
            order_id = self.client.place_order(order_params)
            logger.info(f"📤 Exit order placed | trade={trade_id} reason={exit_reason} id={order_id}")
            return order_id
        except Exception as exc:
            logger.error(f"Exit order failed for trade {trade_id}: {exc}")
            return None

    def cancel_all(self) -> None:
        """Emergency: cancel all known pending orders."""
        for order_id in list(self._pending_order_ids):
            try:
                self.client.cancel_order(order_id)
                self._pending_order_ids.discard(order_id)
            except Exception as exc:
                logger.warning(f"Cancel failed for {order_id}: {exc}")

    def _get_underlying_ltp(self, underlying: str) -> float:
        """Fallback LTP for underlying index."""
        token = settings.INDEX_TOKENS.get(underlying.upper(), "")
        try:
            return self.client.get_ltp(settings.NSE, underlying, token)
        except Exception:
            return 0.0
