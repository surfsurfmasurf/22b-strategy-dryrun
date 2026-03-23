"""
StrategyManager — orchestrates all strategy instances.

Responsibilities:
  - Hold all strategy instances
  - Load per-strategy lifecycle state from SQLite (strategy_state table)
  - run_all(store, regime): call each active strategy, collect signals, route via SignalBus
  - Enforce PAPER mode for every strategy in Phase 2
  - Trigger PaperRecorder.check_positions() each cycle
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Dict, List, Optional

from bot.strategies._base import Signal, StrategyBase
from bot.strategies.signal_bus import SignalBus
from bot.strategies.paper_recorder import PaperRecorder
from bot.strategies.ema_cross import EmaCrossStrategy
from bot.strategies.rsi_exhaustion import RsiExhaustionStrategy
from bot.strategies.range_breakout import RangeBreakoutStrategy

if TYPE_CHECKING:
    from bot.data.store import DataStore

logger = logging.getLogger(__name__)

# Phase 2: only PAPER lifecycle stage is allowed; PAUSED strategies are skipped
ALLOWED_LIFECYCLE = {"PAPER", "SHADOW", "ACTIVE"}


class StrategyManager:
    """
    Manages the full lifecycle of all strategies.

    Usage
    -----
    manager = StrategyManager(store)
    manager.initialize()           # load state from DB, wire bus/recorder
    await manager.run_all(regime)  # called each engine cycle
    """

    def __init__(self, store: "DataStore") -> None:
        self._store = store

        # Core components
        self._bus      = SignalBus(store)
        self._recorder = PaperRecorder(store)

        # Wire recorder into bus
        self._bus.set_paper_recorder(self._recorder)

        # Strategy instances (ordered for deterministic execution)
        self._strategies: List[StrategyBase] = [
            EmaCrossStrategy(),
            RsiExhaustionStrategy(),
            RangeBreakoutStrategy(),
        ]

        # Per-strategy lifecycle state: name -> {"mode": "PAPER" | "PAUSED", ...}
        self._state: Dict[str, dict] = {}

    # ---------------------------------------------------------------------- #
    # Initialization
    # ---------------------------------------------------------------------- #

    def initialize(self) -> None:
        """
        Load (or create) strategy lifecycle records from strategy_state table.
        Called once on startup, before the main loop.
        """
        for strategy in self._strategies:
            existing = self._store.get_strategy_state(strategy.name)
            if existing is None:
                # First run — register strategy as PAPER
                record = {
                    "name":           strategy.name,
                    "mode":           "PAPER",
                    "category":       strategy.category,
                    "regime_filter":  json.dumps(strategy.regime_filter),
                    "stats_json":     "{}",
                    "last_signal_ts": None,
                    "lifecycle_stage": "paper",
                }
                self._store.upsert_strategy_state(record)
                self._state[strategy.name] = record
                logger.info(
                    "[StrategyManager] Registered new strategy: %s (mode=PAPER)",
                    strategy.name,
                )
            else:
                self._state[strategy.name] = dict(existing)
                logger.info(
                    "[StrategyManager] Loaded strategy '%s' (mode=%s)",
                    strategy.name, existing.get("mode", "PAPER"),
                )

    # ---------------------------------------------------------------------- #
    # Main execution cycle
    # ---------------------------------------------------------------------- #

    def run_all(self, regime: dict) -> List[Signal]:
        """
        Run all active strategies and route resulting signals via SignalBus.

        Parameters
        ----------
        regime : dict — current regime dict from RegimeDetector.detect()

        Returns
        -------
        Flat list of all Signal objects produced (accepted + skipped).
        """
        all_signals: List[Signal] = []
        run_ts = int(time.time() * 1000)

        for strategy in self._strategies:
            state = self._state.get(strategy.name, {})
            mode  = state.get("mode", "PAPER")

            # Skip paused strategies
            if mode == "PAUSED":
                logger.debug(
                    "[StrategyManager] Skipping '%s' — PAUSED", strategy.name
                )
                continue

            # Phase 2 enforcement: downgrade any LIVE attempt to PAPER
            if mode == "LIVE":
                logger.warning(
                    "[StrategyManager] Strategy '%s' has mode=LIVE — forcing PAPER (Phase 2)",
                    strategy.name,
                )
                mode = "PAPER"

            try:
                signals = strategy.compute(self._store, regime)
            except Exception as exc:
                logger.error(
                    "[StrategyManager] Error running strategy '%s': %s",
                    strategy.name, exc, exc_info=True,
                )
                signals = []

            if signals:
                # Set signal mode to match the strategy's current mode
                # LIVE is blocked in Phase 2 (already downgraded to PAPER above)
                for sig in signals:
                    if mode in ("PAPER", "SHADOW"):
                        sig.mode = mode
                    elif mode == "LIVE":
                        sig.mode = "LIVE"  # will be blocked by SignalBus in Phase 2
                    else:
                        sig.mode = "PAPER"

                # Route through SignalBus (filters + records)
                self._bus.publish(signals, strategy)

                # Update last_signal_ts in state table for non-SKIP signals
                actionable = [s for s in signals if s.action != "SKIP"]
                if actionable:
                    self._store.upsert_strategy_state({
                        "name":          strategy.name,
                        "last_signal_ts": run_ts,
                    })
                    self._state[strategy.name]["last_signal_ts"] = run_ts

                all_signals.extend(signals)
                logger.debug(
                    "[StrategyManager] '%s' produced %d signal(s) this cycle",
                    strategy.name, len(signals),
                )

        # Check paper positions for TP/SL hits
        self._recorder.check_positions()

        return all_signals

    # ---------------------------------------------------------------------- #
    # Accessors (for API endpoints)
    # ---------------------------------------------------------------------- #

    @property
    def bus(self) -> SignalBus:
        return self._bus

    @property
    def recorder(self) -> PaperRecorder:
        return self._recorder

    def get_strategy_list(self) -> List[dict]:
        """Return a list of strategy info dicts for the dashboard."""
        result = []
        for strategy in self._strategies:
            state = self._state.get(strategy.name, {})
            result.append({
                "name":          strategy.name,
                "category":      strategy.category,
                "regime_filter": strategy.regime_filter,
                "mode":          state.get("mode", "PAPER"),
                "last_signal_ts": state.get("last_signal_ts"),
                "lifecycle_stage": state.get("lifecycle_stage", "paper"),
            })
        return result

    def set_strategy_mode(self, name: str, mode: str) -> bool:
        """
        Update the mode for a strategy in both memory and the DB.

        Parameters
        ----------
        name : str — strategy name
        mode : str — new mode: "PAPER" | "SHADOW" | "PAUSED" | "LIVE"

        Returns True if strategy was found and updated, False otherwise.
        """
        valid_modes = {"PAPER", "SHADOW", "PAUSED", "LIVE"}
        if mode not in valid_modes:
            logger.warning(
                "[StrategyManager] set_strategy_mode: invalid mode '%s' for '%s'",
                mode, name,
            )
            return False

        if name not in self._state:
            logger.warning(
                "[StrategyManager] set_strategy_mode: strategy '%s' not found", name
            )
            return False

        self._state[name]["mode"] = mode
        self._store.upsert_strategy_state({"name": name, "mode": mode})
        logger.info(
            "[StrategyManager] Strategy '%s' mode updated to %s", name, mode
        )
        return True

    def get_bus_stats(self) -> dict:
        return self._bus.get_stats()
