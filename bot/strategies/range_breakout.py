"""
Strategy 3 — Range Breakout (Breakout)

Logic (1H candles, per tracked symbol):
  Define range: highest high and lowest low over the last 24 bars.

  BUY  when current close > range_high
            AND Bollinger Band bandwidth expanding
               (current bb_bw > prev bb_bw * 1.1)
            AND volume > 1.5x 20-period average volume

  SELL when current close < range_low AND same BB/volume conditions

TP  = entry + (range_high - range_low) * 1.0  (measured move)
SL  = range_high * 0.995  (just below breakout level for LONG)
    = range_low  * 1.005  (just above breakdown level for SHORT)

Confidence = volume_ratio / 3.0, clamped to [0.5, 1.0]

Regime filter: BTC_BULLISH, BTC_SIDEWAYS, LOW_VOLATILITY
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

MIN_CANDLES = 50   # need 24 for range + 20 for BB, plus safety margin


class RangeBreakoutStrategy(StrategyBase):
    """
    Breakout strategy based on consolidation range and Bollinger Band expansion.
    """

    name:          str       = "range_breakout"
    category:      str       = "breakout"
    regime_filter: List[str] = ["BTC_BULLISH", "BTC_SIDEWAYS", "LOW_VOLATILITY"]

    # Strategy parameters
    RANGE_BARS:   int   = 24      # bars used to define the consolidation range
    BB_PERIOD:    int   = 20
    BB_STD:       float = 2.0
    BB_EXPAND:    float = 1.1     # current bb_bw must be > prev bb_bw * this factor
    VOL_MULT:     float = 1.5     # volume must exceed 1.5x 20-bar average
    SL_OFFSET:    float = 0.005   # 0.5% buffer inside range for SL
    INTERVAL:     str   = "1h"

    def compute(self, store: "DataStore", regime: dict) -> List[Signal]:
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
                "[range_breakout] Not enough candles for %s (%d < %d)",
                symbol, len(candles), MIN_CANDLES,
            )
            return None

        df = pd.DataFrame(candles).sort_values("ts").reset_index(drop=True)
        close  = df["c"].astype(float)
        high   = df["h"].astype(float)
        low    = df["l"].astype(float)
        volume = df["v"].astype(float)

        # ---- Consolidation range (excluding current bar) ----
        # Use bars [-RANGE_BARS-1 : -1] to avoid look-ahead on the current bar
        range_window_high = high.iloc[-(self.RANGE_BARS + 1): -1]
        range_window_low  = low.iloc[-(self.RANGE_BARS + 1): -1]

        if range_window_high.empty or range_window_low.empty:
            return None

        range_high = float(range_window_high.max())
        range_low  = float(range_window_low.min())
        range_size = range_high - range_low

        if range_size <= 0:
            return None

        # ---- Bollinger Band bandwidth ----
        bb_mid  = close.rolling(self.BB_PERIOD).mean()
        bb_std  = close.rolling(self.BB_PERIOD).std()
        bb_up   = bb_mid + self.BB_STD * bb_std
        bb_lo   = bb_mid - self.BB_STD * bb_std
        bb_bw   = (bb_up - bb_lo) / bb_mid.replace(0, np.nan)

        bb_bw_cur  = bb_bw.iloc[-1]
        bb_bw_prev = bb_bw.iloc[-2]

        if pd.isna(bb_bw_cur) or pd.isna(bb_bw_prev) or bb_bw_prev == 0:
            return None

        bb_expanding = bb_bw_cur > bb_bw_prev * self.BB_EXPAND

        # ---- Volume ratio ----
        vol_avg = volume.rolling(20).mean()
        vol_ratio = float((volume / vol_avg.replace(0, np.nan)).iloc[-1])
        if pd.isna(vol_ratio):
            vol_ratio = 1.0

        # ---- Current price ----
        price_cur = float(close.iloc[-1])

        # ---- BUY breakout ----
        if price_cur > range_high and bb_expanding and vol_ratio >= self.VOL_MULT:
            confidence = self._clamp(vol_ratio / 3.0, 0.5, 1.0)
            tp = round(price_cur + range_size, 8)           # measured move
            sl = round(range_high * (1 - self.SL_OFFSET), 8)  # just below breakout

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
                    f"Breakout above {range_high:.4f} range, "
                    f"Vol={vol_ratio:.1f}x, BB expanding ({bb_bw_cur:.4f}>"
                    f"{bb_bw_prev * self.BB_EXPAND:.4f})"
                ),
            )

        # ---- SELL breakdown ----
        if price_cur < range_low and bb_expanding and vol_ratio >= self.VOL_MULT:
            confidence = self._clamp(vol_ratio / 3.0, 0.5, 1.0)
            tp = round(price_cur - range_size, 8)           # measured move downward
            sl = round(range_low * (1 + self.SL_OFFSET), 8)   # just above breakdown

            return Signal(
                strategy=self.name,
                symbol=symbol,
                action="SELL",
                mode=self._PHASE2_MODE,
                confidence=round(confidence, 4),
                regime=regime,
                tp=tp,
                sl=sl,
                reason=(
                    f"Breakdown below {range_low:.4f} range, "
                    f"Vol={vol_ratio:.1f}x, BB expanding ({bb_bw_cur:.4f}>"
                    f"{bb_bw_prev * self.BB_EXPAND:.4f})"
                ),
            )

        return None   # No breakout this bar
