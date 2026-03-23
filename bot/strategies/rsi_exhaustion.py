"""
Strategy 2 — RSI Exhaustion (Mean Reversion)

Logic (1H candles, per tracked symbol):
  BUY  when RSI(14) < 30 (oversold)
            AND price > EMA200 * 0.95 (within 5% of long-term trend)
            AND RSI recovering (current RSI > previous RSI)
  SELL when RSI(14) > 70 (overbought)
            AND price < EMA200 * 1.05
            AND RSI declining (current RSI < previous RSI)

TP  = entry * 1.02  (+2%)
SL  = entry * 0.99  (-1%)

Confidence:
  BUY  → 1.0 - (RSI / 100)      deeper oversold = higher confidence
  SELL → RSI / 100               deeper overbought = higher confidence

Regime filter: BTC_BEARISH, BTC_SIDEWAYS
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

MIN_CANDLES = 210   # EMA200 requires at least 200 bars


class RsiExhaustionStrategy(StrategyBase):
    """
    Mean-reversion strategy based on RSI extremes vs EMA200.
    """

    name:          str       = "rsi_exhaustion"
    category:      str       = "mean_reversion"
    regime_filter: List[str] = ["BTC_BEARISH", "BTC_SIDEWAYS"]

    # Strategy parameters
    RSI_PERIOD:   int   = 14
    RSI_OVERSOLD:  float = 30.0
    RSI_OVERBOUGHT: float = 70.0
    EMA_LONG:     int   = 200
    EMA_BAND_PCT: float = 0.05    # price must be within 5% of EMA200
    TP_PCT:       float = 0.02    # +2%
    SL_PCT:       float = 0.01    # -1%
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
                "[rsi_exhaustion] Not enough candles for %s (%d < %d)",
                symbol, len(candles), MIN_CANDLES,
            )
            return None

        df = pd.DataFrame(candles).sort_values("ts").reset_index(drop=True)
        close = df["c"].astype(float)

        # EMA 200
        ema200 = close.ewm(span=self.EMA_LONG, adjust=False).mean()

        # RSI(14)
        delta = close.diff()
        gain  = delta.clip(lower=0).ewm(span=self.RSI_PERIOD, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(span=self.RSI_PERIOD, adjust=False).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = (100 - (100 / (1 + rs))).fillna(50)

        rsi_cur    = rsi.iloc[-1]
        rsi_prev   = rsi.iloc[-2]
        price_cur  = close.iloc[-1]
        ema200_cur = ema200.iloc[-1]

        if ema200_cur == 0:
            return None

        price_vs_ema200 = (price_cur / ema200_cur - 1) * 100  # % deviation

        # ---- BUY condition ----
        # RSI oversold + not in deep downtrend + RSI recovering
        if (
            rsi_cur < self.RSI_OVERSOLD
            and price_cur > ema200_cur * (1 - self.EMA_BAND_PCT)
            and rsi_cur > rsi_prev
        ):
            confidence = self._clamp(1.0 - (rsi_cur / 100.0), 0.5, 1.0)
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
                    f"RSI exhaustion: RSI={rsi_cur:.1f} (recovering), "
                    f"price vs EMA200={price_vs_ema200:.1f}%"
                ),
            )

        # ---- SELL condition ----
        # RSI overbought + not in deep uptrend + RSI declining
        if (
            rsi_cur > self.RSI_OVERBOUGHT
            and price_cur < ema200_cur * (1 + self.EMA_BAND_PCT)
            and rsi_cur < rsi_prev
        ):
            confidence = self._clamp(rsi_cur / 100.0, 0.5, 1.0)
            tp = round(price_cur * (1 - self.TP_PCT), 8)
            sl = round(price_cur * (1 + self.SL_PCT), 8)

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
                    f"RSI exhaustion: RSI={rsi_cur:.1f} (declining), "
                    f"price vs EMA200={price_vs_ema200:.1f}%"
                ),
            )

        return None   # No exhaustion signal
