"""
strategies/strategy_manager.py
Orchestrates all enabled strategies, scores signals, and returns the 
highest-confidence valid signal for each market snapshot.
"""
from typing import Optional

from strategies.base_strategy import BaseStrategy, Signal
from strategies.trend_following import TrendFollowingStrategy
from strategies.breakout import BreakoutStrategy
from strategies.vwap_reversal import VWAPReversionStrategy
from config.settings import ENABLED_STRATEGIES, MIN_CONFIDENCE
from utils.logger import get_logger

logger = get_logger(__name__)

_STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "trend_following": TrendFollowingStrategy,
    "breakout": BreakoutStrategy,
    "vwap_reversal": VWAPReversionStrategy,
}


class StrategyManager:
    """
    Runs all enabled strategies against market snapshots and returns
    the best signal. Filters by minimum confidence threshold.
    """

    def __init__(self) -> None:
        self.strategies: list[BaseStrategy] = []
        self._load_strategies()

    def _load_strategies(self) -> None:
        """Instantiate only enabled strategies."""
        for name in ENABLED_STRATEGIES:
            cls = _STRATEGY_REGISTRY.get(name.strip().lower())
            if cls:
                self.strategies.append(cls())
                logger.info(f"✅ Strategy loaded: {name}")
            else:
                logger.warning(f"Unknown strategy '{name}', skipping.")

        if not self.strategies:
            logger.warning("No strategies loaded — check ENABLED_STRATEGIES in .env")

    def evaluate(
        self,
        snapshot: dict,
        already_traded_symbols: set[str],
    ) -> Optional[Signal]:
        """
        Run all strategies and return the highest-confidence valid signal.
        
        Args:
            snapshot: Market data snapshot from MarketDataEngine.get_market_snapshot()
            already_traded_symbols: Set of option symbols already open today (deduplication)
            
        Returns:
            Best Signal or None
        """
        underlying = snapshot.get("symbol", "")
        candidates: list[Signal] = []

        for strategy in self.strategies:
            try:
                signal = strategy.generate_signal(snapshot)
                if signal is None:
                    continue

                # Confidence filter
                if signal.confidence < MIN_CONFIDENCE:
                    logger.debug(
                        f"Signal from {strategy.name} below min confidence "
                        f"({signal.confidence:.2f} < {MIN_CONFIDENCE})"
                    )
                    continue

                candidates.append(signal)

            except Exception as exc:
                logger.error(f"Strategy {strategy.name} error on {underlying}: {exc}", exc_info=True)

        if not candidates:
            return None

        # Sort by confidence descending, pick best
        candidates.sort(key=lambda s: s.confidence, reverse=True)
        best = candidates[0]

        logger.info(
            f"🎯 Best signal: {best.strategy_name} | {underlying} {best.option_type} "
            f"conf={best.confidence:.2f}"
        )
        if len(candidates) > 1:
            others = ", ".join(f"{s.strategy_name}({s.confidence:.2f})" for s in candidates[1:])
            logger.debug(f"   Other signals: {others}")

        return best

    def evaluate_all_symbols(
        self,
        snapshots: list[dict],
        already_traded_symbols: set[str],
    ) -> list[Signal]:
        """
        Evaluate all symbols and return all valid signals (sorted by confidence).
        """
        all_signals: list[Signal] = []
        for snapshot in snapshots:
            signal = self.evaluate(snapshot, already_traded_symbols)
            if signal:
                all_signals.append(signal)

        all_signals.sort(key=lambda s: s.confidence, reverse=True)
        return all_signals
