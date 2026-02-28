"""
data/option_chain.py
OptionChainManager: identifies ATM/OTM strikes and resolves NFO trading symbols.
Falls back to algorithmic symbol construction when API data is unavailable.
"""
import math
from datetime import datetime, date
from typing import Optional

from broker.angel_client import AngelClient
from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


def _round_to_strike_step(price: float, symbol: str) -> int:
    """Round a price to the nearest valid strike step for the given index."""
    step = settings.STRIKE_STEPS.get(symbol.upper(), 50)
    return int(round(price / step) * step)


def _get_nearest_expiry() -> str:
    """
    Returns the nearest weekly/monthly NFO expiry date string in format DDMMMYY.
    For NIFTY: weekly Thursday. For BANKNIFTY: weekly Wednesday.
    This is a simplified heuristic — for production, use Angel One's master contract.
    """
    today = date.today()
    # Find next Thursday (NIFTY weekly)
    days_to_thursday = (3 - today.weekday()) % 7
    if days_to_thursday == 0 and datetime.now().hour >= 15:
        days_to_thursday = 7
    next_exp = today + __import__("datetime").timedelta(days=days_to_thursday)
    return next_exp.strftime("%d%b%y").upper()  # e.g. 28FEB24


def _build_option_symbol(underlying: str, expiry: str, option_type: str, strike: int) -> str:
    """
    Constructs the NFO trading symbol.
    Format: {UNDERLYING}{EXPIRY}{STRIKE}{CE/PE}
    Example: NIFTY28FEB2421900CE
    """
    return f"{underlying.upper()}{expiry}{strike}{option_type.upper()}"


class OptionInfo:
    """Holds details of a specific option contract."""

    def __init__(
        self,
        trading_symbol: str,
        token: str,
        underlying: str,
        option_type: str,
        strike: int,
        expiry: str,
        ltp: float = 0.0,
        iv: float = 0.0,
    ) -> None:
        self.trading_symbol = trading_symbol
        self.token = token
        self.underlying = underlying
        self.option_type = option_type
        self.strike = strike
        self.expiry = expiry
        self.ltp = ltp
        self.iv = iv

    def __repr__(self) -> str:
        return (
            f"<OptionInfo {self.trading_symbol} ltp={self.ltp:.2f} iv={self.iv:.1f}>"
        )


class OptionChainManager:
    """
    Resolves option contracts for trading.
    
    Primary: uses Angel One option chain API.
    Fallback: constructs symbol algorithmically and uses LTP API.
    """

    def __init__(self, client: AngelClient) -> None:
        self.client = client
        self._token_cache: dict[str, str] = {}  # symbol -> token

    # ── Token resolution ─────────────────────────────────────────────────────

    def _get_token(self, trading_symbol: str) -> str:
        """
        Look up instrument token for an NFO symbol.
        In production this would search the Angel One master contract CSV.
        For paper trading / demo, returns a placeholder token.
        """
        if trading_symbol in self._token_cache:
            return self._token_cache[trading_symbol]

        # Attempt to find in master contract (Angel One provides daily CSV)
        # For now, return placeholder — actual implementation requires 
        # downloading master contract from Angel One
        token = f"PAPER_{abs(hash(trading_symbol)) % 99999}"
        self._token_cache[trading_symbol] = token
        logger.debug(f"Token for {trading_symbol}: {token}")
        return token

    # ── ATM Strike ───────────────────────────────────────────────────────────

    def get_atm_strike(self, underlying: str, ltp: float) -> int:
        """Round LTP to nearest valid strike step."""
        return _round_to_strike_step(ltp, underlying)

    def get_otm_strike(self, underlying: str, ltp: float, n_steps: int, option_type: str) -> int:
        """
        Get OTM strike n steps away from ATM.
        CE: ATM + n*step, PE: ATM - n*step
        """
        atm = self.get_atm_strike(underlying, ltp)
        step = settings.STRIKE_STEPS.get(underlying.upper(), 50)
        if option_type.upper() == "CE":
            return atm + n_steps * step
        return atm - n_steps * step

    # ── Option info resolution ────────────────────────────────────────────────

    def get_atm_option(
        self, underlying: str, ltp: float, option_type: str
    ) -> Optional[OptionInfo]:
        """
        Resolve ATM option contract (CE or PE) for trading.
        Returns OptionInfo with symbol, token, and LTP.
        """
        try:
            expiry = _get_nearest_expiry()
            strike = self.get_atm_strike(underlying, ltp)
            symbol = _build_option_symbol(underlying, expiry, option_type, strike)
            token = self._get_token(symbol)

            # Try to get actual LTP from broker
            try:
                option_ltp = self.client.get_ltp(settings.NFO, symbol, token)
            except Exception:
                # Paper trading fallback: estimate premium using 1% of underlying
                option_ltp = round(ltp * 0.01, 2)
                logger.debug(f"Using estimated option LTP: {option_ltp}")

            return OptionInfo(
                trading_symbol=symbol,
                token=token,
                underlying=underlying,
                option_type=option_type.upper(),
                strike=strike,
                expiry=expiry,
                ltp=option_ltp,
            )

        except Exception as exc:
            logger.error(f"get_atm_option({underlying}, {option_type}): {exc}")
            return None

    def get_option_chain_data(
        self, underlying: str, ltp: float, expiry: Optional[str] = None
    ) -> dict[str, OptionInfo]:
        """
        Fetch option chain and return dict of {symbol: OptionInfo} for 
        strikes around ATM (-5 to +5).
        """
        if expiry is None:
            expiry = _get_nearest_expiry()
        
        options: dict[str, OptionInfo] = {}
        atm = self.get_atm_strike(underlying, ltp)
        step = settings.STRIKE_STEPS.get(underlying.upper(), 50)
        
        try:
            chain_data = self.client.get_option_chain(underlying, expiry, atm, 5)
            # Parse API response (structure varies) — basic parsing
            if chain_data and isinstance(chain_data, dict):
                for item in chain_data.get("records", {}).get("data", []):
                    for opt_type in ["CE", "PE"]:
                        opt_data = item.get(opt_type, {})
                        if not opt_data:
                            continue
                        sym = opt_data.get("tradingSymbol", "")
                        info = OptionInfo(
                            trading_symbol=sym,
                            token=str(opt_data.get("token", "")),
                            underlying=underlying,
                            option_type=opt_type,
                            strike=int(item.get("strikePrice", 0)),
                            expiry=expiry,
                            ltp=float(opt_data.get("lastPrice", 0)),
                            iv=float(opt_data.get("impliedVolatility", 0)),
                        )
                        options[sym] = info
        except Exception as exc:
            logger.warning(f"Option chain API failed for {underlying}: {exc}. Using synthetic data.")

        # If no data from API, generate synthetic entries for ATM±2 strikes
        if not options:
            for delta in range(-2, 3):
                for opt_type in ["CE", "PE"]:
                    strike = atm + delta * step
                    sym = _build_option_symbol(underlying, expiry, opt_type, strike)
                    ltp_estimate = max(1.0, round(ltp * 0.008 * (1 + abs(delta) * 0.3), 2))
                    options[sym] = OptionInfo(
                        trading_symbol=sym,
                        token=self._get_token(sym),
                        underlying=underlying,
                        option_type=opt_type,
                        strike=strike,
                        expiry=expiry,
                        ltp=ltp_estimate,
                    )

        return options
