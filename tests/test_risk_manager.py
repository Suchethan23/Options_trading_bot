"""
tests/test_risk_manager.py
Unit tests for risk management logic.
"""
import os
os.environ["PAPER_TRADING"] = "True"
os.environ["CAPITAL"] = "100000"
os.environ["MAX_RISK_PER_TRADE_PCT"] = "1.0"
os.environ["MAX_DAILY_LOSS_PCT"] = "3.0"
os.environ["MAX_OPEN_TRADES"] = "3"
os.environ["SL_PCT"] = "30"
os.environ["TARGET_PCT"] = "60"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["LOG_FILE"] = "logs/trading.log"

import pytest
from unittest.mock import patch, MagicMock

from risk.position_sizing import PositionSizer
from strategies.base_strategy import Signal


class TestPositionSizing:
    def setup_method(self):
        self.sizer = PositionSizer()

    def test_basic_sizing_nifty(self):
        # CAPITAL=100000, risk=1% → risk_amount=1000
        # lot_size=50, ltp=100 → premium_per_lot=5000 → 0.2 lots → 1 lot (min)
        qty = self.sizer.calculate_quantity("NIFTY", option_ltp=100.0)
        assert qty == 1

    def test_cheap_option_allows_more_lots(self):
        # ltp=10, lot_size=50 → premium_per_lot=500 → 1000/500=2 lots
        qty = self.sizer.calculate_quantity("NIFTY", option_ltp=10.0)
        assert qty == 2

    def test_zero_ltp_returns_one_lot(self):
        qty = self.sizer.calculate_quantity("NIFTY", option_ltp=0.0)
        assert qty == 1

    def test_lot_size_banknifty(self):
        assert self.sizer.get_lot_size("BANKNIFTY") == 15

    def test_lot_size_unknown_defaults_50(self):
        assert self.sizer.get_lot_size("UNKNOWN") == 50

    def test_risk_amount(self):
        assert abs(self.sizer.max_risk_amount(100_000) - 1000.0) < 0.01


class TestRiskManagerLevels:
    def test_sl_target_computation(self):
        from risk.risk_manager import RiskManager
        from strategies.base_strategy import Signal

        rm = RiskManager()
        sig = Signal(
            underlying="NIFTY",
            option_type="CE",
            strategy_name="test",
            confidence=0.75,
            sl_pct=30.0,
            target_pct=60.0,
            reason="test_reason",
        )
        sl, target = rm.compute_sl_target(sig, option_ltp=100.0)
        assert abs(sl - 70.0) < 0.01
        assert abs(target - 160.0) < 0.01

    def test_sl_cannot_be_negative(self):
        from risk.risk_manager import RiskManager
        from strategies.base_strategy import Signal

        rm = RiskManager()
        sig = Signal(
            underlying="NIFTY",
            option_type="CE",
            strategy_name="test",
            confidence=0.75,
            sl_pct=200.0,  # extreme SL
            target_pct=60.0,
            reason="test_reason",
        )
        sl, _ = rm.compute_sl_target(sig, option_ltp=10.0)
        assert sl >= 0.05  # must be non-negative


class TestSignal:
    def test_signal_creation_ce(self):
        sig = Signal(
            underlying="NIFTY",
            option_type="CE",
            strategy_name="trend_following",
            confidence=0.75,
            sl_pct=30.0,
            target_pct=60.0,
            reason="EMA aligned",
        )
        assert sig.option_type == "CE"
        assert sig.confidence == 0.75

    def test_signal_invalid_confidence(self):
        with pytest.raises(AssertionError):
            Signal(
                underlying="NIFTY",
                option_type="CE",
                strategy_name="test",
                confidence=1.5,  # > 1.0
                sl_pct=30,
                target_pct=60,
                reason="invalid",
            )

    def test_signal_invalid_option_type(self):
        with pytest.raises(AssertionError):
            Signal(
                underlying="NIFTY",
                option_type="CALL",  # must be CE or PE
                strategy_name="test",
                confidence=0.7,
                sl_pct=30,
                target_pct=60,
                reason="invalid",
            )
