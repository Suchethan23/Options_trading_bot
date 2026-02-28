"""
config/settings.py
Loads all configuration from environment variables (.env file).
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _get_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("true", "1", "yes")


def _get_float(key: str, default: float = 0.0) -> float:
    return float(os.getenv(key, str(default)))


def _get_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


def _get_list(key: str, default: str = "") -> list[str]:
    raw = os.getenv(key, default)
    return [s.strip() for s in raw.split(",") if s.strip()]


# ── Broker credentials ─────────────────────────────────────────────────────
ANGEL_API_KEY: str = os.getenv("ANGEL_API_KEY", "")
ANGEL_CLIENT_ID: str = os.getenv("ANGEL_CLIENT_ID", "")
ANGEL_PIN: str = os.getenv("ANGEL_PIN", "")
ANGEL_TOTP_SECRET: str = os.getenv("ANGEL_TOTP_SECRET", "")

# ── Trading mode ────────────────────────────────────────────────────────────
PAPER_TRADING: bool = _get_bool("PAPER_TRADING", True)

# ── Capital & risk ──────────────────────────────────────────────────────────
CAPITAL: float = _get_float("CAPITAL", 100_000)
MAX_RISK_PER_TRADE_PCT: float = _get_float("MAX_RISK_PER_TRADE_PCT", 1.0)
MAX_DAILY_LOSS_PCT: float = _get_float("MAX_DAILY_LOSS_PCT", 3.0)
MAX_OPEN_TRADES: int = _get_int("MAX_OPEN_TRADES", 3)
SL_PCT: float = _get_float("SL_PCT", 30.0)          # % of option premium
TARGET_PCT: float = _get_float("TARGET_PCT", 60.0)   # % of option premium
TRAIL_SL_PCT: float = _get_float("TRAIL_SL_PCT", 20.0)

# ── Symbols ─────────────────────────────────────────────────────────────────
SYMBOLS: list[str] = _get_list("SYMBOLS", "NIFTY,BANKNIFTY")

# Instrument token mapping for index underlyings (NSE)
INDEX_TOKENS: dict[str, str] = {
    "NIFTY": "99926000",
    "BANKNIFTY": "99926009",
    "FINNIFTY": "99926037",
    "MIDCPNIFTY": "99926074",
}

# Lot sizes for instruments
LOT_SIZES: dict[str, int] = {
    "NIFTY": 50,
    "BANKNIFTY": 15,
    "FINNIFTY": 40,
    "MIDCPNIFTY": 75,
}

# Option strike step
STRIKE_STEPS: dict[str, int] = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "FINNIFTY": 50,
    "MIDCPNIFTY": 25,
}

# ── Strategy configuration ───────────────────────────────────────────────────
ENABLED_STRATEGIES: list[str] = _get_list(
    "ENABLED_STRATEGIES", "trend_following,breakout,vwap_reversal"
)
MIN_CONFIDENCE: float = _get_float("MIN_CONFIDENCE", 0.65)

# ── Database ────────────────────────────────────────────────────────────────
DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/trading.db")

# ── Telegram ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Logging ─────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE: str = os.getenv("LOG_FILE", str(BASE_DIR / "logs" / "trading.log"))

# ── Market hours (IST) ───────────────────────────────────────────────────────
MARKET_OPEN_HOUR: int = 9
MARKET_OPEN_MINUTE: int = 15
MARKET_CLOSE_HOUR: int = 15
MARKET_CLOSE_MINUTE: int = 30

# Auto-exit all positions before market close
AUTO_EXIT_HOUR: int = 15
AUTO_EXIT_MINUTE: int = 20

# Exchange
NSE = "NSE"
NFO = "NFO"

# Candle intervals
INTERVAL_1MIN = "ONE_MINUTE"
INTERVAL_5MIN = "FIVE_MINUTE"
INTERVAL_15MIN = "FIFTEEN_MINUTE"
INTERVAL_1DAY = "ONE_DAY"

# ── Validation ───────────────────────────────────────────────────────────────
def validate_config() -> None:
    """Raise if required keys are missing for live mode."""
    if not PAPER_TRADING:
        required = {
            "ANGEL_API_KEY": ANGEL_API_KEY,
            "ANGEL_CLIENT_ID": ANGEL_CLIENT_ID,
            "ANGEL_PIN": ANGEL_PIN,
            "ANGEL_TOTP_SECRET": ANGEL_TOTP_SECRET,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise EnvironmentError(
                f"Live trading requires these env vars: {', '.join(missing)}"
            )
