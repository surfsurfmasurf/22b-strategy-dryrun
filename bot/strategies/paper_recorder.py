"""
PaperRecorder — tracks paper trading positions and computes performance metrics.

Responsibilities:
  - Open a paper position when a BUY or SELL signal arrives
  - Monitor prices from DataStore to detect TP/SL hits
  - Close positions and record PnL to SQLite (positions table, mode='PAPER')
  - Compute per-strategy stats: Win Rate, Profit Factor, MDD, Expectancy
  - Broadcast position updates to dashboard via DataStore._broadcast()
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from bot.data.store import DataStore
    from bot.strategies._base import Signal

logger = logging.getLogger(__name__)

# Fraction of price used as default SL when strategy provides none
DEFAULT_SL_PCT = 0.015   # 1.5%
DEFAULT_TP_PCT = 0.030   # 3.0%


# --------------------------------------------------------------------------- #
# In-memory position model
# --------------------------------------------------------------------------- #

@dataclass
class PaperPosition:
    """Represents a single open or closed paper position."""

    id:           str
    strategy:     str
    symbol:       str
    side:         str         # LONG or SHORT
    entry_price:  float
    qty:          float       # nominal unit qty (1.0 for paper)
    tp:           Optional[float]
    sl:           Optional[float]
    opened_at:    int         # unix ms
    regime:       str
    signal_id:    str

    # Mutable after open
    status:       str  = "OPEN"   # OPEN | CLOSED
    closed_at:    Optional[int]   = None
    exit_price:   Optional[float] = None
    pnl_pct:      Optional[float] = None
    close_reason: str             = ""

    def to_dict(self) -> dict:
        return {
            "id":           self.id,
            "strategy":     self.strategy,
            "symbol":       self.symbol,
            "side":         self.side,
            "entry_price":  self.entry_price,
            "qty":          self.qty,
            "tp":           self.tp,
            "sl":           self.sl,
            "opened_at":    self.opened_at,
            "regime":       self.regime,
            "signal_id":    self.signal_id,
            "status":       self.status,
            "closed_at":    self.closed_at,
            "exit_price":   self.exit_price,
            "pnl_pct":      self.pnl_pct,
            "close_reason": self.close_reason,
        }


# --------------------------------------------------------------------------- #
# PaperRecorder
# --------------------------------------------------------------------------- #

class PaperRecorder:
    """
    Manages the lifecycle of paper positions.

    Usage
    -----
    recorder = PaperRecorder(store)
    recorder.on_signal(signal)           # called by SignalBus
    await recorder.check_positions()     # called by StrategyManager each cycle
    stats = recorder.get_strategy_stats()
    """

    def __init__(self, store: "DataStore") -> None:
        self._store = store
        # In-memory open positions: id -> PaperPosition
        self._open: Dict[str, PaperPosition] = {}

    # ---------------------------------------------------------------------- #
    # Signal intake
    # ---------------------------------------------------------------------- #

    def _get_entry_price(self, symbol: str) -> float:
        """Get best available price: ticker first, then last candle close."""
        ticker = self._store.get_ticker(symbol)
        if ticker:
            price = float(ticker.get("price", 0.0))
            if price > 0:
                return price
        # Fallback: last 1h candle close
        candles = self._store.get_candles(symbol, "1h", limit=1)
        if candles:
            price = float(candles[-1].get("c", 0.0))
            if price > 0:
                logger.debug("[PaperRecorder] Using candle fallback price %.4f for %s", price, symbol)
                return price
        return 0.0

    def on_signal(self, signal: "Signal") -> None:
        """
        Process an accepted signal from SignalBus.

        BUY  → open LONG paper position
        SELL → open SHORT paper position (or close any open LONG for same symbol/strategy)
        """
        entry_price = self._get_entry_price(signal.symbol)
        if entry_price <= 0:
            logger.warning(
                "[PaperRecorder] No price available for %s — cannot open position", signal.symbol
            )
            return

        # --- Check if there is an existing open position for this symbol+strategy ---
        existing = self._find_open(signal.symbol, signal.strategy)

        if signal.action == "BUY":
            if existing:
                logger.debug(
                    "[PaperRecorder] Already open LONG for %s/%s — ignoring BUY",
                    signal.symbol, signal.strategy,
                )
                return
            self._open_position(signal, entry_price, "LONG")

        elif signal.action == "SELL":
            # If an existing LONG is open for this strategy, close it first
            if existing and existing.side == "LONG":
                self._close_position(existing, entry_price, "SELL signal")
            elif existing and existing.side == "SHORT":
                logger.debug(
                    "[PaperRecorder] Already open SHORT for %s/%s — ignoring SELL",
                    signal.symbol, signal.strategy,
                )
                return
            else:
                # Open a new SHORT
                self._open_position(signal, entry_price, "SHORT")

    # ---------------------------------------------------------------------- #
    # Position monitoring (called each engine cycle)
    # ---------------------------------------------------------------------- #

    def check_positions(self) -> None:
        """
        Scan open positions and close any that have hit TP or SL
        based on the latest ticker price from DataStore.

        This is a synchronous method — safe to call from the async main loop
        because DataStore.get_ticker() is non-blocking.
        """
        to_close: List[PaperPosition] = []

        for pos in list(self._open.values()):
            ticker = self._store.get_ticker(pos.symbol)
            if ticker is None:
                continue
            price = float(ticker.get("price", 0.0))
            if price <= 0:
                continue

            if pos.side == "LONG":
                if pos.tp is not None and price >= pos.tp:
                    to_close.append((pos, price, "TP hit"))
                elif pos.sl is not None and price <= pos.sl:
                    to_close.append((pos, price, "SL hit"))
            elif pos.side == "SHORT":
                if pos.tp is not None and price <= pos.tp:
                    to_close.append((pos, price, "TP hit"))
                elif pos.sl is not None and price >= pos.sl:
                    to_close.append((pos, price, "SL hit"))

        for pos, price, reason in to_close:
            self._close_position(pos, price, reason)

    # ---------------------------------------------------------------------- #
    # Stats (for dashboard Panel 4)
    # ---------------------------------------------------------------------- #

    def get_strategy_stats(self) -> Dict[str, dict]:
        """
        Compute per-strategy performance metrics from closed positions stored in SQLite.

        Returns a dict keyed by strategy name:
        {
          "ema_cross": {
            "win_rate": 0.62,
            "profit_factor": 1.8,
            "trade_count": 25,
            "mdd": -0.034,
            "expectancy": 0.012,
            "open_count": 2,
          },
          ...
        }
        """
        return self._store.get_strategy_stats()

    def get_open_positions(self) -> List[dict]:
        """Return all open paper positions as dicts."""
        return [p.to_dict() for p in self._open.values()]

    # ---------------------------------------------------------------------- #
    # Internal helpers
    # ---------------------------------------------------------------------- #

    def _open_position(self, signal: "Signal", entry_price: float, side: str) -> None:
        """Create and register a new paper position."""
        tp = signal.tp
        sl = signal.sl

        # Apply defaults if strategy didn't provide TP/SL
        if tp is None:
            if side == "LONG":
                tp = round(entry_price * (1 + DEFAULT_TP_PCT), 8)
            else:
                tp = round(entry_price * (1 - DEFAULT_TP_PCT), 8)
        if sl is None:
            if side == "LONG":
                sl = round(entry_price * (1 - DEFAULT_SL_PCT), 8)
            else:
                sl = round(entry_price * (1 + DEFAULT_SL_PCT), 8)

        pos = PaperPosition(
            id=str(uuid.uuid4()),
            strategy=signal.strategy,
            symbol=signal.symbol,
            side=side,
            entry_price=entry_price,
            qty=1.0,
            tp=tp,
            sl=sl,
            opened_at=int(time.time() * 1000),
            regime=signal.regime,
            signal_id=signal.id,
        )
        self._open[pos.id] = pos

        # Persist to SQLite
        self._store.save_paper_position(pos.to_dict())

        # Broadcast to dashboard
        self._store._broadcast("paper_position_opened", pos.to_dict())

        logger.info(
            "[PaperRecorder] Opened %s %s @ %.8f  TP=%.8f  SL=%.8f  [%s]",
            side, signal.symbol, entry_price, tp, sl, signal.strategy,
        )

    def _close_position(
        self, pos: PaperPosition, exit_price: float, reason: str
    ) -> None:
        """Close an open position and record PnL."""
        if pos.side == "LONG":
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
        else:  # SHORT
            pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100

        pos.status = "CLOSED"
        pos.closed_at = int(time.time() * 1000)
        pos.exit_price = exit_price
        pos.pnl_pct = round(pnl_pct, 4)
        pos.close_reason = reason

        # Remove from open dict
        self._open.pop(pos.id, None)

        # Persist update to SQLite
        self._store.update_paper_position(pos.id, {
            "status":       pos.status,
            "closed_at":    pos.closed_at,
            "exit_price":   pos.exit_price,
            "pnl_pct":      pos.pnl_pct,
            "close_reason": pos.close_reason,
        })

        # Broadcast position closed event
        self._store._broadcast("paper_position_closed", pos.to_dict())

        outcome = "WIN" if pnl_pct > 0 else "LOSS"
        logger.info(
            "[PaperRecorder] Closed %s %s — %s  PnL=%.2f%%  reason=%s  [%s]",
            pos.side, pos.symbol, outcome, pnl_pct, reason, pos.strategy,
        )

    def _find_open(self, symbol: str, strategy: str) -> Optional[PaperPosition]:
        """Find any open position for this symbol + strategy combination."""
        for pos in self._open.values():
            if pos.symbol == symbol and pos.strategy == strategy:
                return pos
        return None
