"""
SignalBus — central router for strategy signals.

Responsibilities (Phase 2):
  - Receive Signal objects from strategies
  - Validate mode (only PAPER allowed in Phase 2)
  - Check regime compatibility (strategy.is_allowed_in_regime)
  - Route accepted signals to PaperRecorder
  - Broadcast accepted signals to DataStore (for dashboard Panel 2)
  - Maintain an in-memory deque of the last 100 signals
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING, Deque, List, Optional

from bot.strategies._base import Signal

if TYPE_CHECKING:
    from bot.data.store import DataStore
    from bot.strategies.paper_recorder import PaperRecorder
    from bot.strategies._base import StrategyBase

logger = logging.getLogger(__name__)

# Maximum signals kept in memory for quick dashboard reads
MAX_SIGNALS_MEMORY = 100

# In Phase 2, PAPER and SHADOW modes are accepted; LIVE signals are blocked
ALLOWED_MODES_PHASE2 = {"PAPER", "SHADOW"}


class SignalBus:
    """
    Routes signals from strategies to the PaperRecorder and DataStore.

    Usage
    -----
    bus = SignalBus(store)
    bus.set_paper_recorder(recorder)   # called by StrategyManager after init
    bus.publish(signals, strategy)     # called per strategy run
    """

    def __init__(self, store: "DataStore") -> None:
        self._store = store
        self._recorder: Optional["PaperRecorder"] = None
        self._memory: Deque[dict] = deque(maxlen=MAX_SIGNALS_MEMORY)

        # Cumulative counters
        self._total_received:  int = 0
        self._total_accepted:  int = 0
        self._total_rejected:  int = 0

    # ---------------------------------------------------------------------- #
    # Wiring
    # ---------------------------------------------------------------------- #

    def set_paper_recorder(self, recorder: "PaperRecorder") -> None:
        """Inject PaperRecorder reference after construction."""
        self._recorder = recorder

    # ---------------------------------------------------------------------- #
    # Public API
    # ---------------------------------------------------------------------- #

    def publish(self, signals: List[Signal], strategy: "StrategyBase") -> None:
        """
        Process a batch of signals from one strategy.

        Filters:
          1. Mode must be PAPER (Phase 2 enforcement)
          2. Strategy must be allowed in the signal's regime
          3. Action must not be SKIP (SKIP signals are noted but not routed)

        Accepted signals are:
          - Stored in memory deque
          - Persisted to SQLite via store.save_signal()
          - Broadcast to dashboard via store._broadcast("signal", ...)
          - Forwarded to PaperRecorder
        """
        for signal in signals:
            self._total_received += 1

            # --- Phase 2 mode gate ---
            if signal.mode not in ALLOWED_MODES_PHASE2:
                logger.warning(
                    "[SignalBus] Rejected signal from '%s': mode='%s' not allowed in Phase 2",
                    strategy.name, signal.mode,
                )
                self._total_rejected += 1
                continue

            # --- SKIP signals: log and discard ---
            if signal.action == "SKIP":
                logger.debug(
                    "[SignalBus] SKIP signal from '%s' for %s — reason: %s",
                    strategy.name, signal.symbol, signal.reason,
                )
                self._total_rejected += 1
                continue

            # --- Regime compatibility ---
            if not strategy.is_allowed_in_regime(signal.regime):
                logger.info(
                    "[SignalBus] Filtered signal from '%s': not allowed in regime '%s'",
                    strategy.name, signal.regime,
                )
                self._total_rejected += 1
                continue

            # --- Accept ---
            self._total_accepted += 1
            sig_dict = signal.to_dict()
            self._memory.append(sig_dict)

            # Persist to SQLite
            self._store.save_signal(sig_dict)

            # Broadcast to dashboard WebSocket subscribers
            self._store._broadcast("signal", sig_dict)

            # Forward to PaperRecorder
            if self._recorder is not None:
                self._recorder.on_signal(signal)

            logger.info(
                "[SignalBus] Accepted %s signal: %s %s @ regime=%s conf=%.2f — %s",
                signal.mode, signal.action, signal.symbol,
                signal.regime, signal.confidence, signal.reason,
            )

    # ---------------------------------------------------------------------- #
    # Memory read (for dashboard Panel 2)
    # ---------------------------------------------------------------------- #

    def get_recent_signals(self, limit: int = 50) -> List[dict]:
        """Return the most recent signals (newest first)."""
        items = list(self._memory)
        items.reverse()
        return items[:limit]

    # ---------------------------------------------------------------------- #
    # Stats
    # ---------------------------------------------------------------------- #

    def get_stats(self) -> dict:
        return {
            "total_received": self._total_received,
            "total_accepted": self._total_accepted,
            "total_rejected": self._total_rejected,
        }

    def __repr__(self) -> str:
        return (
            f"<SignalBus accepted={self._total_accepted} "
            f"rejected={self._total_rejected}>"
        )
