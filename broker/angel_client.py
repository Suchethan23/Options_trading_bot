"""
broker/angel_client.py
Angel One SmartAPI wrapper. Handles authentication, token refresh,
market data fetching, order placement, and WebSocket live ticks.
"""
import time
import pyotp
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from utils.logger import get_logger
from config import settings

logger = get_logger(__name__)


class AngelClient:
    """
    Wrapper around smartapi-python. All public methods raise on failure
    (caller is responsible for handling exceptions).
    
    Usage:
        client = AngelClient()
        client.login()
        ltp = client.get_ltp("NFO", "NIFTY28FEB24C21900", "43215")
    """

    def __init__(self) -> None:
        self._smart_api = None   # SmartConnect instance
        self._ws = None          # SmartWebSocket instance
        self._refresh_token: str = ""
        self._feed_token: str = ""
        self._jwt_token: str = ""
        self._login_time: Optional[datetime] = None
        self._lock = threading.Lock()

    # ── Authentication ────────────────────────────────────────────────────────

    def login(self) -> None:
        """
        Authenticate with Angel One SmartAPI using TOTP.
        Must be called before any other method.
        """
        try:
            from SmartApi import SmartConnect  # type: ignore

            self._smart_api = SmartConnect(api_key=settings.ANGEL_API_KEY)

            totp_code = pyotp.TOTP(settings.ANGEL_TOTP_SECRET).now()
            data = self._smart_api.generateSession(
                settings.ANGEL_CLIENT_ID,
                settings.ANGEL_PIN,
                totp_code,
            )

            if data.get("status") is False:
                raise RuntimeError(f"Login failed: {data.get('message', 'Unknown error')}")

            self._jwt_token = data["data"]["jwtToken"]
            self._refresh_token = data["data"]["refreshToken"]
            self._feed_token = self._smart_api.getfeedToken()
            self._login_time = datetime.now()

            logger.info(f"✅ Angel One login successful for {settings.ANGEL_CLIENT_ID}")

        except ImportError:
            raise ImportError(
                "smartapi-python is not installed. Run: pip install smartapi-python"
            )
        except Exception as exc:
            logger.error(f"Login error: {exc}")
            raise

    def refresh_token(self) -> None:
        """Refresh JWT token using stored refresh token."""
        try:
            data = self._smart_api.generateToken(self._refresh_token)
            if data.get("status") is False:
                logger.warning("Token refresh failed, re-logging in...")
                self.login()
                return
            self._jwt_token = data["data"]["jwtToken"]
            self._refresh_token = data["data"]["refreshToken"]
            self._login_time = datetime.now()
            logger.info("🔄 JWT token refreshed successfully")
        except Exception as exc:
            logger.warning(f"Token refresh error: {exc}. Re-logging in...")
            self.login()

    def _ensure_session(self) -> None:
        """Auto-refresh token if older than 6 hours."""
        if self._login_time is None:
            raise RuntimeError("Client not logged in. Call login() first.")
        if datetime.now() - self._login_time > timedelta(hours=6):
            logger.info("Session nearing expiry, refreshing token...")
            self.refresh_token()

    # ── Market Data ───────────────────────────────────────────────────────────

    def get_ltp(self, exchange: str, symbol: str, token: str) -> float:
        """
        Fetch last traded price for a symbol.
        
        Args:
            exchange: "NSE" or "NFO"
            symbol: Trading symbol e.g. "NIFTY-EQ" or "NIFTY28FEB24C21900"
            token: Instrument token string
            
        Returns:
            LTP as float
        """
        self._ensure_session()
        try:
            data = self._smart_api.ltpData(exchange, symbol, token)
            if data.get("status") is False:
                raise RuntimeError(f"LTP fetch failed: {data.get('message')}")
            return float(data["data"]["ltp"])
        except Exception as exc:
            logger.error(f"get_ltp({symbol}): {exc}")
            raise

    def get_candles(
        self,
        token: str,
        symbol: str,
        interval: str,
        from_date: str,
        to_date: str,
        exchange: str = "NSE",
    ) -> list[dict]:
        """
        Fetch OHLCV candles.
        
        Args:
            token: Instrument token
            symbol: Trading symbol
            interval: e.g. "FIVE_MINUTE", "ONE_DAY"
            from_date: "YYYY-MM-DD HH:MM"
            to_date: "YYYY-MM-DD HH:MM"
            exchange: NSE or NFO
            
        Returns:
            List of dicts with keys: timestamp, open, high, low, close, volume
        """
        self._ensure_session()
        try:
            params = {
                "exchange": exchange,
                "symboltoken": token,
                "interval": interval,
                "fromdate": from_date,
                "todate": to_date,
            }
            data = self._smart_api.getCandleData(params)
            if data.get("status") is False:
                raise RuntimeError(f"Candle fetch failed: {data.get('message')}")
            candles = data.get("data", [])
            return [
                {
                    "timestamp": c[0],
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                }
                for c in candles
            ]
        except Exception as exc:
            logger.error(f"get_candles({symbol}, {interval}): {exc}")
            raise

    def get_option_chain(
        self,
        symbol: str,
        expiry: str,
        strike_price: float,
        strike_range: int = 5,
    ) -> dict:
        """
        Fetch option chain for a symbol.
        
        Args:
            symbol: e.g. "NIFTY"
            expiry: e.g. "28Feb2024"
            strike_price: ATM strike reference
            strike_range: Number of strikes on each side of ATM
            
        Returns:
            Raw option chain data dict
        """
        self._ensure_session()
        try:
            data = self._smart_api.getOptionChain(
                mode="FULL",
                exchangeSegment="nse_fo",
                underlyingToken=settings.INDEX_TOKENS.get(symbol, ""),
                strike=int(strike_price),
                depth=strike_range,
            )
            if data.get("status") is False:
                raise RuntimeError(f"Option chain failed: {data.get('message')}")
            return data.get("data", {})
        except Exception as exc:
            logger.error(f"get_option_chain({symbol}): {exc}")
            raise

    # ── Order Management ──────────────────────────────────────────────────────

    def place_order(self, params: dict) -> str:
        """
        Place an order on Angel One.
        
        Args:
            params: Order dict with keys:
                variety, tradingsymbol, symboltoken, transactiontype,
                exchange, ordertype, producttype, duration, price,
                squareoff, stoploss, quantity
                
        Returns:
            Order ID string
        """
        self._ensure_session()
        try:
            data = self._smart_api.placeOrder(params)
            if data.get("status") is False:
                raise RuntimeError(f"Order placement failed: {data.get('message')}")
            order_id = data["data"]["orderid"]
            logger.info(
                f"📋 Order placed | {params.get('transactiontype')} "
                f"{params.get('tradingsymbol')} qty={params.get('quantity')} "
                f"| orderid={order_id}"
            )
            return order_id
        except Exception as exc:
            logger.error(f"place_order failed: {exc} | params={params}")
            raise

    def modify_order(self, order_id: str, params: dict) -> bool:
        """Modify an existing order."""
        self._ensure_session()
        try:
            params["orderid"] = order_id
            data = self._smart_api.modifyOrder(params)
            success = data.get("status") is not False
            logger.info(f"✏️  Modify order {order_id}: {'OK' if success else 'FAILED'}")
            return success
        except Exception as exc:
            logger.error(f"modify_order({order_id}): {exc}")
            raise

    def cancel_order(self, order_id: str, variety: str = "NORMAL") -> bool:
        """Cancel an order by ID."""
        self._ensure_session()
        try:
            data = self._smart_api.cancelOrder(order_id, variety)
            success = data.get("status") is not False
            logger.info(f"❌ Cancel order {order_id}: {'OK' if success else 'FAILED'}")
            return success
        except Exception as exc:
            logger.error(f"cancel_order({order_id}): {exc}")
            raise

    def get_positions(self) -> list[dict]:
        """Get current open positions."""
        self._ensure_session()
        try:
            data = self._smart_api.position()
            if data.get("status") is False:
                return []
            return data.get("data", []) or []
        except Exception as exc:
            logger.error(f"get_positions: {exc}")
            return []

    def get_orderbook(self) -> list[dict]:
        """Get today's order book."""
        self._ensure_session()
        try:
            data = self._smart_api.orderBook()
            if data.get("status") is False:
                return []
            return data.get("data", []) or []
        except Exception as exc:
            logger.error(f"get_orderbook: {exc}")
            return []

    def get_funds(self) -> dict:
        """Get available margin/funds."""
        self._ensure_session()
        try:
            data = self._smart_api.rmsLimit()
            return data.get("data", {}) or {}
        except Exception as exc:
            logger.error(f"get_funds: {exc}")
            return {}

    # ── WebSocket Live Feed ────────────────────────────────────────────────────

    def subscribe_ticks(
        self,
        tokens: list[dict],
        on_tick: Callable[[dict], None],
    ) -> None:
        """
        Subscribe to live tick feed via WebSocket.
        
        Args:
            tokens: List of {"exchangeType": 1, "tokens": ["token1", "token2"]}
            on_tick: Callback function called with each tick dict
            
        This method starts a background thread and returns immediately.
        """
        def _on_data(wsapp, message: str) -> None:
            try:
                import json
                tick = json.loads(message) if isinstance(message, str) else message
                on_tick(tick)
            except Exception as exc:
                logger.warning(f"Tick parse error: {exc}")

        def _on_error(wsapp, error: str) -> None:
            logger.error(f"WebSocket error: {error}")

        def _on_close(wsapp, close_status_code: Any, close_msg: Any) -> None:
            logger.warning(f"WebSocket closed: {close_status_code} {close_msg}")

        def _on_open(wsapp) -> None:
            logger.info("🔌 WebSocket connected, subscribing to ticks...")
            wsapp.subscribe("abc123", 1, tokens)

        def _run_ws() -> None:
            try:
                from SmartApi.smartWebSocketV2 import SmartWebSocketV2  # type: ignore
                self._ws = SmartWebSocketV2(
                    settings.ANGEL_API_KEY,
                    self._feed_token,
                    settings.ANGEL_CLIENT_ID,
                    self._jwt_token,
                )
                self._ws.on_open = _on_open
                self._ws.on_error = _on_error
                self._ws.on_close = _on_close
                self._ws.on_message = _on_data
                self._ws.connect()
            except Exception as exc:
                logger.error(f"WebSocket thread error: {exc}")

        thread = threading.Thread(target=_run_ws, daemon=True, name="angel-ws")
        thread.start()
        logger.info("🔌 WebSocket thread started")

    def close_websocket(self) -> None:
        """Gracefully close WebSocket connection."""
        if self._ws:
            try:
                self._ws.close_connection()
                logger.info("WebSocket closed")
            except Exception as exc:
                logger.warning(f"WS close error: {exc}")


# ── Paper Trading Mock Client ─────────────────────────────────────────────────

class PaperAngelClient(AngelClient):
    """
    Mock client for paper trading. Simulates all API calls without hitting
    the Angel One servers. Safe to use without real API credentials.
    """

    def __init__(self) -> None:
        super().__init__()
        self._paper_order_counter = 1000
        logger.info("📄 PAPER TRADING MODE — No real orders will be placed")

    def login(self) -> None:
        self._login_time = datetime.now()
        logger.info("📄 Paper client: login simulated")

    def refresh_token(self) -> None:
        self._login_time = datetime.now()
        logger.info("📄 Paper client: token refresh simulated")

    def get_ltp(self, exchange: str, symbol: str, token: str) -> float:
        """Returns a simulated LTP — override in tests for specific values."""
        import random
        base_prices = {
            "99926000": 22000.0,   # NIFTY spot
            "99926009": 48000.0,   # BANKNIFTY spot
        }
        base = base_prices.get(token, 100.0)
        return round(base + random.uniform(-base * 0.01, base * 0.01), 2)

    def get_candles(self, token, symbol, interval, from_date, to_date, exchange="NSE"):
        """Returns synthetic OHLCV candles for paper trading."""
        import random
        from datetime import datetime, timedelta

        candles = []
        base = 22000.0 if "NIFTY" in symbol.upper() else 48000.0
        now = datetime.now()
        for i in range(50):
            ts = now - timedelta(minutes=5 * (50 - i))
            open_ = base + random.uniform(-100, 100)
            close_ = open_ + random.uniform(-50, 50)
            high_ = max(open_, close_) + random.uniform(0, 30)
            low_ = min(open_, close_) - random.uniform(0, 30)
            vol = random.randint(100_000, 500_000)
            candles.append({
                "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
                "open": round(open_, 2),
                "high": round(high_, 2),
                "low": round(low_, 2),
                "close": round(close_, 2),
                "volume": vol,
            })
        return candles

    def get_option_chain(self, symbol, expiry, strike_price, strike_range=5):
        """Returns synthetic option chain data."""
        return {"records": []}

    def place_order(self, params: dict) -> str:
        self._paper_order_counter += 1
        order_id = f"PAPER{self._paper_order_counter}"
        logger.info(
            f"📄 PAPER ORDER | {params.get('transactiontype')} "
            f"{params.get('tradingsymbol')} qty={params.get('quantity')} "
            f"price={params.get('price')} | id={order_id}"
        )
        return order_id

    def modify_order(self, order_id: str, params: dict) -> bool:
        logger.info(f"📄 PAPER MODIFY ORDER {order_id}")
        return True

    def cancel_order(self, order_id: str, variety: str = "NORMAL") -> bool:
        logger.info(f"📄 PAPER CANCEL ORDER {order_id}")
        return True

    def get_positions(self) -> list[dict]:
        return []

    def get_orderbook(self) -> list[dict]:
        return []

    def get_funds(self) -> dict:
        from config.settings import CAPITAL
        return {"availablecash": str(CAPITAL)}

    def subscribe_ticks(self, tokens, on_tick):
        logger.info("📄 Paper client: WebSocket ticks simulated (no-op)")

    def close_websocket(self) -> None:
        pass


def get_client() -> AngelClient:
    """
    Factory: returns PaperAngelClient if PAPER_TRADING=True, 
    else real AngelClient.
    """
    if settings.PAPER_TRADING:
        return PaperAngelClient()
    return AngelClient()
