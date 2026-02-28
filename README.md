# 🤖 Angel One Options Trading Agent

Autonomous, production-grade Python trading agent for **NSE equity options** using Angel One SmartAPI.  
**Strategies are options-buying only (long CE / PE)**. Paper trading is on by default — no real money at risk.

---

## Architecture

```
trading_agent/
├── config/         settings.py — env-driven config
├── broker/         angel_client.py — Angel One API + Paper mock
├── data/           market_data.py, option_chain.py — candles + indicators
├── strategies/     trend_following, breakout, vwap_reversal, manager
├── risk/           risk_manager.py, position_sizing.py
├── execution/      order_manager.py, position_manager.py
├── storage/        database.py, models.py (SQLite/Postgres)
├── utils/          logger.py, indicators.py, telegram.py
├── tests/          pytest unit tests (no API key needed)
└── main.py         APScheduler 1-min loop
```

---

## Prerequisites

- Python 3.11+
- Angel One SmartAPI account with TOTP 2FA enabled
- Angel One API key (from [smartapi.angelbroking.com](https://smartapi.angelbroking.com))

---

## Installation

```bash
# 1. Clone / open project folder
cd c:\Users\skarra\angel_one_option_trading_bot

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

# 3. Install dependencies
pip install -r requirements.txt
```

---

## Configuration

```bash
# Copy example env file
copy .env.example .env      # Windows
# cp .env.example .env      # Linux/macOS
```

Edit `.env` with your details:

```env
# Angel One credentials (leave blank for paper trading)
ANGEL_API_KEY=your_api_key
ANGEL_CLIENT_ID=your_client_id
ANGEL_PIN=your_mpin
ANGEL_TOTP_SECRET=your_totp_secret   # base32 string from authenticator app

# Trading mode (True = safe, no real orders)
PAPER_TRADING=True

# Capital & risk
CAPITAL=100000
MAX_RISK_PER_TRADE_PCT=1.0     # 1% of capital per trade
MAX_DAILY_LOSS_PCT=3.0         # Halt after 3% daily loss
MAX_OPEN_TRADES=3              # Max concurrent positions

# Options - SL and target as % of option premium
SL_PCT=30.0                    # Exit if option drops 30%
TARGET_PCT=60.0                # Exit if option gains 60%
TRAIL_SL_PCT=20.0              # Trailing SL once 30% gain reached

# Symbols to trade
SYMBOLS=NIFTY,BANKNIFTY

# Strategies (comma-separated)
ENABLED_STRATEGIES=trend_following,breakout,vwap_reversal
```

---

## Paper Trading (Recommended First)

Paper trading uses a mock broker — **no Angel One credentials needed**.

```bash
python main.py --paper
```

Expected output:
```
2024-02-28 09:15:00 | INFO     | __main__ | 🚀 Angel One Options Trading Agent Starting
2024-02-28 09:15:00 | INFO     | __main__ |    Mode    : 📄 PAPER TRADING
2024-02-28 09:15:00 | INFO     | __main__ |    Capital : ₹1,00,000
2024-02-28 09:15:01 | INFO     | broker.angel_client | 📄 PAPER TRADING MODE — No real orders will be placed
2024-02-28 09:16:00 | INFO     | __main__ | ⏱  Cycle start: 09:16:00 IST
2024-02-28 09:16:01 | INFO     | strategies.strategy_manager | 🎯 Best signal: trend_following | NIFTY CE conf=0.72
2024-02-28 09:16:01 | INFO     | execution.order_manager | 📄 PAPER ORDER | BUY NIFTY28FEB24C22000 qty=50 price=87.50
```

Trades are saved to `trading.db` (SQLite). View with any SQLite browser.

---

## Live Trading

> ⚠️ **Only proceed when you fully understand the risks. Start with small capital.**

```bash
# Set in .env first:
# PAPER_TRADING=False
# (plus all Angel One credentials)

python main.py --live
```

---

## Running Tests (No API Key Required)

```bash
pip install pytest
pytest tests/ -v
```

Tests cover:
- `test_indicators.py` — EMA, RSI, ATR, Bollinger, VWAP, volume spike
- `test_risk_manager.py` — position sizing math, SL/target computation
- `test_strategies.py` — signal generation with synthetic OHLCV data

---

## Strategies

| Strategy | Signal Condition | Buy |
|---|---|---|
| **Trend Following** | EMA21>EMA50>EMA200, price pulls back to EMA21, RSI>55 | ATM CE |
| **Trend Following** | EMA21<EMA50<EMA200, price bounces to EMA21, RSI<45 | ATM PE |
| **Breakout** | Price > prev day high + volume spike | ATM CE |
| **Breakdown** | Price < prev day low + volume spike | ATM PE |
| **VWAP Reversion** | Price crossed above VWAP after >0.5% deviation | ATM CE |
| **VWAP Reversion** | Price crossed below VWAP after >0.5% above | ATM PE |

---

## Risk Management

| Rule | Value | Configurable |
|---|---|---|
| Max risk per trade | 1% of capital → premium outlay | `MAX_RISK_PER_TRADE_PCT` |
| Daily loss cap | 3% of capital → halt trading | `MAX_DAILY_LOSS_PCT` |
| Max open trades | 3 concurrent positions | `MAX_OPEN_TRADES` |
| Stop loss | 30% of option premium paid | `SL_PCT` |
| Target | 60% of option premium paid | `TARGET_PCT` |
| Trailing SL | Activates at 30% gain, trails 20% | `TRAIL_SL_PCT` |
| Auto-exit | All positions closed at 3:20 PM IST | Fixed |
| Min confidence | Signal must score ≥ 0.65 | `MIN_CONFIDENCE` |

---

## Optional: Telegram Alerts

1. Create a bot via [@BotFather](https://t.me/BotFather)
2. Get your chat ID from [@userinfobot](https://t.me/userinfobot)
3. Set in `.env`:
   ```env
   TELEGRAM_TOKEN=123456:ABCdef...
   TELEGRAM_CHAT_ID=987654321
   ```

You'll receive alerts for: trade entry, SL hit, target hit, daily PnL summary, risk halts.

---

## Database

SQLite (`trading.db`) is created automatically. Tables:

- **trades** — full trade lifecycle (entry, exit, PnL, strategy)
- **signals** — every signal generated (traded or skipped + reason)
- **daily_pnl** — end-of-day PnL summary

Switch to PostgreSQL anytime:
```env
DATABASE_URL=postgresql://user:pass@localhost:5432/trading
```

---

## Important Notes

- **Option token lookup**: The `data/option_chain.py` uses algorithmic symbol construction for paper trading. For live trading, download Angel One's master contract CSV daily and implement token lookup from it. The code has a placeholder `_get_token()` method for this.
- **TOTP secret**: Found in your authenticator app as the base32 seed (not the 6-digit code).
- **Market hours**: Agent only places trades Mon–Fri, 9:15 AM – 3:30 PM IST.
- **Lot sizes**: NIFTY=50, BANKNIFTY=15, FINNIFTY=40. Update in `config/settings.py` if changed by NSE.
