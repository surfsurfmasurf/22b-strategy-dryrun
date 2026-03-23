"""
Strategy 1 — EMA Cross (Trend Following)

Logic (1H candles, per tracked symbol):
  BUY  when EMA20 crosses above EMA50
       AND RSI(14) in [45, 65]
       AND volume > 1.2x 20-period average
  SELL when EMA20 crosses below EMA50

TP  = entry * 1.03  (+3%)
SL  = entry * 0.985 (-1.5%)

Confidence is scaled by RSI position within [45, 65]:
  0.5 + (RSI - 45) / 40 * 0.5  capped to [0.5, 1.0]

Regime filter: BTC_BULLISH, ALT_ROTATION
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List

import numpy as np
import pandas as pd

from bot.strategies._base import Signal, StrategyBase

if TYPE_CHECKING:
    from bot.data.store import DataStore

logger = logging.getLogger(__name__)

# Minimum candles required for reliable indicator computation
MIN_CANDLES = 55   # EMA50 needs 50+, plus 2-bar lookback for cross detection


class EmaCrossStrategy(StrategyBase):
    """
    Trend-following strategy that fires on EMA20 / EMA50 crossovers.
    """

    name:          str       = "ema_cross"
    category:      str       = "trend"
    regime_filter: List[str] = ["BTC_BULLISH", "ALT_ROTATION"]

    # Strategy parameters
    EMA_FAST:     int   = 20
    EMA_SLOW:     int   = 50
    RSI_PERIOD:   int   = 14
    RSI_LOW:      float = 45.0
    RSI_HIGH:     float = 65.0
    VOL_MULT:     float = 1.2    # volume must exceed this multiple of its 20-bar avg
    TP_PCT:       float = 0.03   # +3%
    SL_PCT:       float = 0.015  # -1.5%
    INTERVAL:     str   = "1h"

    def compute(self, store: "DataStore", regime: dict) -> List[Signal]:
        """
        Evaluate EMA-cross logic for every tracked symbol.

        Returns a list of Signal objects (BUY, SELL, or SKIP).
        """
        from bot.config import get_config
        config = get_config()

        current_regime = regime.get("regime", "UNKNOWN")
        signals: List[Signal] = []

        for symbol in config.tracked_symbols:
            sig = self._evaluate_symbol(store, symbol, current_regime)
            if sig is not None:
                signals.append(sig)

        return signals

    # ---------------------------------------------------------------------- #
    # Per-symbol logic
    # ---------------------------------------------------------------------- #

    def _evaluate_symbol(
        self, store: "DataStore", symbol: str, regime: str
    ) -> Signal | None:
        candles = store.get_candles(symbol, self.INTERVAL, limit=MIN_CANDLES + 10)
        if len(candles) < MIN_CANDLES:
            logger.debug(
                "[ema_cross] Not enough candles for %s (%d < %d)",
                symbol, len(candles), MIN_CANDLES,
            )
            return None

        df = pd.DataFrame(candles).sort_values("ts").reset_index(drop=True)
        close  = df["c"].astype(float)
        volume = df["v"].astype(float)

        # Compute indicators
        ema_fast = close.ewm(span=self.EMA_FAST, adjust=False).mean()
        ema_slow = close.ewm(span=self.EMA_SLOW, adjust=False).mean()

        # RSI
        delta = close.diff()
        gain  = delta.clip(lower=0).ewm(span=self.RSI_PERIOD, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(span=self.RSI_PERIOD, adjust=False).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = (100 - (100 / (1 + rs))).fillna(50)

        # Volume ratio vs 20-period average
        vol_avg = volume.rolling(20).mean()
        vol_ratio = (volume / vol_avg.replace(0, np.nan)).fillna(1.0)

        # Current and previous bar values (iloc[-1] = current, iloc[-2] = previous)
        ema_fast_cur  = ema_fast.iloc[-1]
        ema_fast_prev = ema_fast.iloc[-2]
        ema_slow_cur  = ema_slow.iloc[-1]
        ema_slow_prev = ema_slow.iloc[-2]
        rsi_cur       = rsi.iloc[-1]
        vol_ratio_cur = vol_ratio.iloc[-1]
        price_cur     = close.iloc[-1]

        # ---- BUY condition ----
        # EMA20 crossed above EMA50 (prev: fast < slow, current: fast > slow)
        cross_up = (ema_fast_prev < ema_slow_prev) and (ema_fast_cur > ema_slow_cur)

        if cross_up:
            # RSI filter
            if not (self.RSI_LOW <= rsi_cur <= self.RSI_HIGH):
                logger.debug(
                    "[ema_cross] %s: BUY cross detected but RSI=%.1f outside [%.0f, %.0f]",
                    symbol, rsi_cur, self.RSI_LOW, self.RSI_HIGH,
                )
                return Signal(
                    strategy=self.name, symbol=symbol, action="SKIP",
                    mode=self._PHASE2_MODE, confidence=0.0, regime=regime,
                    reason=f"EMA cross up but RSI={rsi_cur:.1f} outside filter",
                )

            # Volume filter
            if vol_ratio_cur < self.VOL_MULT:
                logger.debug(
                    "[ema_cross] %s: BUY cross detected but vol ratio=%.2fx < %.1fx",
                    symbol, vol_ratio_cur, self.VOL_MULT,
                )
                return Signal(
                    strategy=self.name, symbol=symbol, action="SKIP",
                    mode=self._PHASE2_MODE, confidence=0.0, regime=regime,
                    reason=f"EMA cross up but vol={vol_ratio_cur:.2f}x < {self.VOL_MULT}x",
                )

            # Confidence: 0.5 + (RSI - 45) / 40 * 0.5, clamped to [0.5, 1.0]
            confidence = self._clamp(
                0.5 + (rsi_cur - self.RSI_LOW) / 40.0 * 0.5, 0.5, 1.0
            )
            tp = round(price_cur * (1 + self.TP_PCT), 8)
            sl = round(price_cur * (1 - self.SL_PCT), 8)

            return Signal(
                strategy=self.name,
                symbol=symbol,
                action="BUY",
                mode=self._PHASE2_MODE,
                confidence=round(confidence, 4),
                regime=regime,
                tp=tp,
                sl=sl,
                reason=(
                    f"EMA{self.EMA_FAST} crossed above EMA{self.EMA_SLOW}, "
                    f"RSI={rsi_cur:.1f}, Vol={vol_ratio_cur:.1f}x"
                ),
            )

        # ---- SELL / close condition ----
        # EMA20 crossed below EMA50
        cross_down = (ema_fast_prev > ema_slow_prev) and (ema_fast_cur < ema_slow_cur)

        if cross_down:
            return Signal(
                strategy=self.name,
                symbol=symbol,
                action="SELL",
                mode=self._PHASE2_MODE,
                confidence=0.6,
                regime=regime,
                reason=(
                    f"EMA{self.EMA_FAST} crossed below EMA{self.EMA_SLOW}, "
                    f"RSI={rsi_cur:.1f}"
                ),
            )

        return None   # No cross — nothing to signal this bar
