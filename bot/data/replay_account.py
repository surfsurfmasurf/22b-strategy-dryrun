"""
ReplayAccount — virtual account tracker for replay-based backtesting.

Tracks:
  - Virtual USDT balance (starting from configurable initial capital)
  - Per-trade absolute PnL after fee + slippage
  - Equity curve and maximum drawdown
  - Per-strategy cumulative performance
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Immutable record of a completed simulated trade."""
    position_id:  str
    strategy:     str
    symbol:       str
    side:         str          # LONG | SHORT
    entry_price:  float
    exit_price:   float
    qty_usdt:     float        # notional USDT size at entry
    pnl_usdt:     float        # net PnL after fee + slippage
    pnl_pct:      float        # net PnL % (relative to notional)
    fee_usdt:     float        # total fee paid (entry + exit)
    slippage_usdt: float       # total slippage cost
    opened_at_ms: int
    closed_at_ms: int
    close_reason: str

    @property
    def duration_ms(self) -> int:
        return self.closed_at_ms - self.opened_at_ms

    @property
    def duration_hours(self) -> float:
        return self.duration_ms / 3_600_000


class ReplayAccount:
    """
    Virtual account for replay / paper-trading simulation.

    Usage
    -----
    account = ReplayAccount(initial_balance=10_000.0)
    qty_usdt = account.open_position(position_id, strategy, symbol, side, entry_price, opened_at_ms)
    account.close_position(position_id, exit_price, closed_at_ms, close_reason)
    report = account.get_report()
    """

    def __init__(
        self,
        initial_balance: float = 10_000.0,
        position_size_pct: float = 0.10,   # 10% of balance per trade
        fee_rate: float = 0.0004,           # 0.04% taker fee per side
        slippage_pct: float = 0.0005,       # 0.05% market impact per side
    ) -> None:
        self._initial_balance = initial_balance
        self._balance = initial_balance
        self._position_size_pct = position_size_pct
        self._fee_rate = fee_rate
        self._slippage_pct = slippage_pct

        # open_positions: position_id → dict with entry info
        self._open: Dict[str, dict] = {}

        # completed trades
        self._trades: List[TradeRecord] = []

        # equity curve: list of (ts_ms, balance)
        self._equity_curve: List[tuple] = [(0, initial_balance)]

        # per-strategy tracking
        self._strategy_balances: Dict[str, float] = {}

        logger.info(
            "[ReplayAccount] Initialized: balance=%.2f  size_pct=%.1f%%  fee=%.4f%%  slippage=%.4f%%",
            initial_balance,
            position_size_pct * 100,
            fee_rate * 100,
            slippage_pct * 100,
        )

    # ---------------------------------------------------------------------- #
    # Position lifecycle
    # ---------------------------------------------------------------------- #

    def open_position(
        self,
        position_id: str,
        strategy: str,
        symbol: str,
        side: str,
        entry_price: float,
        opened_at_ms: int,
    ) -> float:
        """
        Register a new open position.
        Returns the notional USDT size allocated (after slippage/fee deducted).
        """
        if position_id in self._open:
            logger.warning("[ReplayAccount] Position %s already open — ignoring duplicate open", position_id)
            return 0.0

        # Allocate portion of current balance
        notional = self._balance * self._position_size_pct

        # Entry slippage cost (paid immediately)
        slippage_entry = notional * self._slippage_pct
        # Entry fee (paid immediately)
        fee_entry = notional * self._fee_rate

        entry_cost = slippage_entry + fee_entry
        self._balance -= entry_cost

        self._open[position_id] = {
            "strategy":    strategy,
            "symbol":      symbol,
            "side":        side,
            "entry_price": entry_price,
            "qty_usdt":    notional,
            "fee_entry":   fee_entry,
            "slippage_entry": slippage_entry,
            "opened_at_ms": opened_at_ms,
        }

        logger.debug(
            "[ReplayAccount] Open %s %s %s  notional=%.2f  entry_cost=%.4f  balance=%.2f",
            side, symbol, strategy, notional, entry_cost, self._balance,
        )
        return notional

    def close_position(
        self,
        position_id: str,
        exit_price: float,
        closed_at_ms: int,
        close_reason: str,
    ) -> Optional[TradeRecord]:
        """
        Close an open position and settle PnL.
        Returns the completed TradeRecord, or None if position_id not found.
        """
        pos = self._open.pop(position_id, None)
        if pos is None:
            logger.warning("[ReplayAccount] Close called for unknown position %s", position_id)
            return None

        entry_price = pos["entry_price"]
        notional    = pos["qty_usdt"]
        side        = pos["side"]

        # Raw PnL %
        if side == "LONG":
            raw_pnl_pct = (exit_price - entry_price) / entry_price
        else:
            raw_pnl_pct = (entry_price - exit_price) / entry_price

        # Gross PnL in USDT
        gross_pnl_usdt = notional * raw_pnl_pct

        # Exit fee + slippage
        exit_notional = notional + gross_pnl_usdt  # approximate exit value
        fee_exit      = exit_notional * self._fee_rate
        slippage_exit = exit_notional * self._slippage_pct

        total_fee       = pos["fee_entry"]      + fee_exit
        total_slippage  = pos["slippage_entry"] + slippage_exit

        # Net PnL
        net_pnl_usdt = gross_pnl_usdt - fee_exit - slippage_exit
        net_pnl_pct  = (net_pnl_usdt / notional) * 100

        # Update balance (add back notional + net_pnl)
        self._balance += notional + net_pnl_usdt

        # Equity curve snapshot
        self._equity_curve.append((closed_at_ms, self._balance))

        record = TradeRecord(
            position_id   = position_id,
            strategy      = pos["strategy"],
            symbol        = pos["symbol"],
            side          = side,
            entry_price   = entry_price,
            exit_price    = exit_price,
            qty_usdt      = notional,
            pnl_usdt      = round(net_pnl_usdt, 6),
            pnl_pct       = round(net_pnl_pct, 4),
            fee_usdt      = round(total_fee, 6),
            slippage_usdt = round(total_slippage, 6),
            opened_at_ms  = pos["opened_at_ms"],
            closed_at_ms  = closed_at_ms,
            close_reason  = close_reason,
        )
        self._trades.append(record)

        outcome = "WIN" if net_pnl_usdt > 0 else "LOSS"
        logger.info(
            "[ReplayAccount] Closed %s %s %s  PnL=%.2f USDT (%.2f%%)  %s  fee=%.4f  balance=%.2f",
            side, pos["symbol"], pos["strategy"],
            net_pnl_usdt, net_pnl_pct, outcome,
            total_fee, self._balance,
        )
        return record

    # ---------------------------------------------------------------------- #
    # Accessors
    # ---------------------------------------------------------------------- #

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def initial_balance(self) -> float:
        return self._initial_balance

    @property
    def trades(self) -> List[TradeRecord]:
        return list(self._trades)

    @property
    def equity_curve(self) -> List[tuple]:
        return list(self._equity_curve)

    def open_count(self) -> int:
        return len(self._open)

    # ---------------------------------------------------------------------- #
    # Metrics helpers (used by BacktestReporter)
    # ---------------------------------------------------------------------- #

    def compute_metrics(self) -> dict:
        """Compute aggregate performance metrics from all completed trades."""
        trades = self._trades
        n = len(trades)
        if n == 0:
            return {"trade_count": 0}

        pnl_list    = [t.pnl_usdt for t in trades]
        wins        = [p for p in pnl_list if p > 0]
        losses      = [p for p in pnl_list if p <= 0]

        total_pnl   = sum(pnl_list)
        win_rate    = len(wins) / n
        avg_win     = sum(wins)   / len(wins)   if wins   else 0.0
        avg_loss    = abs(sum(losses) / len(losses)) if losses else 1e-9
        profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")

        total_return_pct = (self._balance - self._initial_balance) / self._initial_balance * 100

        # Duration stats
        durations = [t.duration_hours for t in trades]
        avg_duration_h = sum(durations) / n

        # MDD from equity curve
        mdd_pct = self._calc_mdd()

        # Expectancy per trade (USDT)
        expectancy = total_pnl / n

        # Sharpe (daily returns proxy using trade PnL)
        import statistics
        pnl_std = statistics.stdev(pnl_list) if n > 1 else 0.0
        sharpe = (total_pnl / n) / pnl_std if pnl_std > 0 else 0.0

        # Per-strategy breakdown
        strat_map: Dict[str, List[float]] = {}
        for t in trades:
            strat_map.setdefault(t.strategy, []).append(t.pnl_usdt)

        per_strategy = {}
        for sname, pnls in strat_map.items():
            sw = [p for p in pnls if p > 0]
            sl = [p for p in pnls if p <= 0]
            per_strategy[sname] = {
                "trade_count":    len(pnls),
                "win_rate":       round(len(sw) / len(pnls), 4),
                "total_pnl_usdt": round(sum(pnls), 4),
                "profit_factor":  round(sum(sw) / abs(sum(sl)), 4) if sl and sum(sl) != 0 else None,
            }

        return {
            "trade_count":       n,
            "win_count":         len(wins),
            "loss_count":        len(losses),
            "win_rate":          round(win_rate, 4),
            "total_pnl_usdt":    round(total_pnl, 4),
            "total_return_pct":  round(total_return_pct, 4),
            "final_balance":     round(self._balance, 4),
            "initial_balance":   round(self._initial_balance, 4),
            "avg_win_usdt":      round(avg_win, 4),
            "avg_loss_usdt":     round(avg_loss, 4),
            "profit_factor":     round(profit_factor, 4) if profit_factor != float("inf") else None,
            "expectancy_usdt":   round(expectancy, 4),
            "mdd_pct":           round(mdd_pct, 4),
            "sharpe_ratio":      round(sharpe, 4),
            "avg_duration_hours": round(avg_duration_h, 2),
            "total_fee_usdt":    round(sum(t.fee_usdt for t in trades), 4),
            "total_slippage_usdt": round(sum(t.slippage_usdt for t in trades), 4),
            "per_strategy":      per_strategy,
        }

    def _calc_mdd(self) -> float:
        """Maximum drawdown as percentage from equity curve."""
        if len(self._equity_curve) < 2:
            return 0.0
        peak = self._equity_curve[0][1]
        mdd  = 0.0
        for _, eq in self._equity_curve:
            if eq > peak:
                peak = eq
            dd = (eq - peak) / peak * 100
            if dd < mdd:
                mdd = dd
        return mdd
