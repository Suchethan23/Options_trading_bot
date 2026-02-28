"""
utils/telegram.py
Optional Telegram alert integration. Silently no-ops if token/chat_id not set.
"""
import threading
from utils.logger import get_logger

logger = get_logger(__name__)


def send_message(text: str) -> None:
    """
    Send a Telegram message asynchronously.
    Silently skips if TELEGRAM_TOKEN or TELEGRAM_CHAT_ID is not configured.
    """
    try:
        from config.settings import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            return

        import requests

        def _send() -> None:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
            try:
                resp = requests.post(url, json=payload, timeout=10)
                if not resp.ok:
                    logger.warning(f"Telegram send failed: {resp.text}")
            except Exception as exc:
                logger.warning(f"Telegram error: {exc}")

        threading.Thread(target=_send, daemon=True).start()

    except Exception as exc:
        logger.warning(f"Telegram module error: {exc}")


def alert_trade(action: str, symbol: str, qty: int, price: float, strategy: str) -> None:
    """Formatted trade alert."""
    emoji = "🟢" if action.upper() == "BUY" else "🔴"
    send_message(
        f"{emoji} <b>TRADE ALERT</b>\n"
        f"Action  : {action}\n"
        f"Symbol  : {symbol}\n"
        f"Qty     : {qty}\n"
        f"Price   : ₹{price:.2f}\n"
        f"Strategy: {strategy}"
    )


def alert_pnl(realized_pnl: float, unrealized_pnl: float) -> None:
    """End-of-day PnL alert."""
    emoji = "💰" if (realized_pnl + unrealized_pnl) >= 0 else "📉"
    send_message(
        f"{emoji} <b>DAILY PNL SUMMARY</b>\n"
        f"Realized  : ₹{realized_pnl:.2f}\n"
        f"Unrealized: ₹{unrealized_pnl:.2f}\n"
        f"Total     : ₹{realized_pnl + unrealized_pnl:.2f}"
    )


def alert_risk_halt(reason: str) -> None:
    """Alert when trading is halted due to risk limits."""
    send_message(f"⛔ <b>TRADING HALTED</b>\nReason: {reason}")
