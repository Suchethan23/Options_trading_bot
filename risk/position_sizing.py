"""
risk/position_sizing.py
Calculate lot quantity based on fixed fractional risk (1% of capital).

For options buying:
  Risk per trade = Capital × risk_pct / 100
  We cap the total premium outlay at risk_per_trade.
  qty_lots = floor(risk_per_trade / (option_ltp × lot_size))
"""
import math

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class PositionSizer:
    """
    Calculates order quantity in lots such that the maximum loss
    (premium paid, since we're buying options) ≤ risk per trade.
    
    Options buying: max loss = premium paid (if option goes to zero).
    So we cap premium outlay at risk_per_trade = CAPITAL × risk_pct%.
    """

    @staticmethod
    def calculate_quantity(
        underlying: str,
        option_ltp: float,
        capital: float = settings.CAPITAL,
        risk_pct: float = settings.MAX_RISK_PER_TRADE_PCT,
    ) -> int:
        """
        Calculate number of lots to buy.
        
        Args:
            underlying: e.g. "NIFTY"
            option_ltp: Current option premium (LTP)
            capital: Total trading capital
            risk_pct: % of capital to risk per trade
            
        Returns:
            Number of lots (minimum 1)
        """
        lot_size = settings.LOT_SIZES.get(underlying.upper(), 50)
        risk_amount = capital * risk_pct / 100

        if option_ltp <= 0:
            logger.warning(f"Invalid option LTP {option_ltp} for {underlying}, defaulting to 1 lot")
            return 1

        # Premium outlay per lot
        premium_per_lot = option_ltp * lot_size

        # Number of lots such that total outlay ≤ risk_amount
        qty_lots = math.floor(risk_amount / premium_per_lot)
        qty_lots = max(1, qty_lots)  # At least 1 lot

        total_premium = qty_lots * premium_per_lot
        logger.info(
            f"📐 Position size | {underlying} | LTP={option_ltp:.2f} "
            f"lot={lot_size} risk=₹{risk_amount:.0f} → {qty_lots} lot(s) "
            f"(outlay ₹{total_premium:.0f})"
        )
        return qty_lots

    @staticmethod
    def get_lot_size(underlying: str) -> int:
        return settings.LOT_SIZES.get(underlying.upper(), 50)

    @staticmethod
    def max_risk_amount(capital: float = settings.CAPITAL) -> float:
        return capital * settings.MAX_RISK_PER_TRADE_PCT / 100
