"""
tests/smoke_test.py
End-to-end smoke test for paper trading mode — no API keys required.
Uses pytest tmp_path fixture for clean DB file handling on Windows.
"""
import os
import pytest


def _set_env(db_path: str) -> None:
    os.environ["PAPER_TRADING"] = "True"
    os.environ["CAPITAL"] = "100000"
    os.environ["SYMBOLS"] = "NIFTY"
    os.environ["ENABLED_STRATEGIES"] = "trend_following,breakout,vwap_reversal"
    os.environ["MIN_CONFIDENCE"] = "0.65"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["LOG_FILE"] = "logs/trading.log"
    os.environ["SL_PCT"] = "30"
    os.environ["TARGET_PCT"] = "60"
    os.environ["MAX_OPEN_TRADES"] = "3"
    os.environ["MAX_RISK_PER_TRADE_PCT"] = "1.0"
    os.environ["MAX_DAILY_LOSS_PCT"] = "3.0"


def test_paper_trading_smoke(tmp_path):
    """Full end-to-end smoke test: DB → client → data → strategies → risk → trade."""
    db_file = str(tmp_path / "smoke.db")
    _set_env(db_file)

    # Import AFTER env is set so settings picks up values
    from importlib import reload
    import config.settings as cfg_mod
    reload(cfg_mod)

    from storage.database import init_db, DatabaseManager, _engine, SessionLocal, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Use the tmp db directly
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    from broker.angel_client import PaperAngelClient
    from data.market_data import MarketDataEngine
    from data.option_chain import OptionChainManager
    from strategies.strategy_manager import StrategyManager
    from risk.risk_manager import RiskManager
    from risk.position_sizing import PositionSizer
    from strategies.base_strategy import Signal

    client = PaperAngelClient()
    client.login()

    mde = MarketDataEngine(client)
    oc = OptionChainManager(client)
    sm = StrategyManager()
    rm = RiskManager()
    ps = PositionSizer()

    # Verify strategies loaded
    assert len(sm.strategies) == 3
    assert {s.name for s in sm.strategies} == {"trend_following", "breakout", "vwap_reversal"}

    # Position sizing
    qty = ps.calculate_quantity("NIFTY", option_ltp=80.0)
    assert qty >= 1

    # Market snapshot
    snap = mde.get_market_snapshot("NIFTY", "99926000")
    assert snap["ltp"] is not None
    assert snap["ltp"] > 0
    assert not snap["df_5min"].empty

    # ATM option resolution
    opt = oc.get_atm_option("NIFTY", snap["ltp"], "CE")
    assert opt is not None
    assert opt.option_type == "CE"
    assert opt.strike > 0
    assert opt.ltp > 0

    # Risk level computation
    sig = Signal(
        underlying="NIFTY",
        option_type="CE",
        strategy_name="smoke",
        confidence=0.75,
        sl_pct=30.0,
        target_pct=60.0,
        reason="smoke test",
    )
    sl, tgt = rm.compute_sl_target(sig, option_ltp=100.0)
    assert abs(sl - 70.0) < 0.01
    assert abs(tgt - 160.0) < 0.01

    # Paper order placement
    order_id = client.place_order({
        "variety": "NORMAL",
        "tradingsymbol": "NIFTY28FEB24C22000",
        "symboltoken": "TEST123",
        "transactiontype": "BUY",
        "exchange": "NFO",
        "ordertype": "MARKET",
        "producttype": "CARRYFORWARD",
        "duration": "DAY",
        "price": "0",
        "squareoff": "0",
        "stoploss": "0",
        "quantity": "50",
    })
    assert order_id.startswith("PAPER")

    print("\n🎉 Smoke test passed — paper trading pipeline fully functional")

