"""
DryRunExecutor — simulates order execution against live market data.

Replaces the real Executor when DRY_RUN_ENABLED=true.
- Receives signals from the strategy pipeline exactly like the real Executor
- Uses live ticker prices from DataStore (real Binance data)
- Records simulated fills into ReplayAccount (virtual balance, fees, slippage)
- Persists simulated orders to SQLite for audit trail
- Never sends any request to the exchange

This enables realistic forward-testing on live data without risking capital.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Dict, List, Optional, Set

if TYPE_CHECKING:
    from bot.config import Config
    from bot.data.store import DataStore
    from bot.data.replay_account import ReplayAccount
    from bot.execution.state_machine import OrderStateMachine
    from bot.execution.kill_switch import KillSwitch
    from bot.strategies._base import Signal

logger = logging.getLogger(__name__)


class DryRunExecutor:
    """
    Simulated executor for dry-run mode.

    Drop-in replacement for Executor — same interface, no real API calls.
    Uses live prices from DataStore and tracks virtual PnL via ReplayAccount.
    """

    def __init__(
        self,
        config: "Config",
        store: "DataStore",
        state_machine: "OrderStateMachine",
        kill_switch: "KillSwitch",
        replay_account: "ReplayAccount",
    ) -> None:
        self._config = config
        self._store = store
        self._sm = state_machine
        self._kill_switch = kill_switch
        self._account = replay_account

        self._submitted_signals: Set[str] = set()
        self._simulated_orders: List[dict] = []

    # ------------------------------------------------------------------ #
    # Lifecycle (no-op — no HTTP client needed)
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        logger.info("[DryRunExecutor] Started (no real exchange connection).")

    async def stop(self) -> None:
        logger.info(
            "[DryRunExecutor] Stopped. Total simulated orders: %d",
            len(self._simulated_orders),
        )

    # ------------------------------------------------------------------ #
    # Simulated order submission
    # ------------------------------------------------------------------ #

    async def submit_order(
        self,
        signal: "Signal",
        qty: Optional[float] = None,
    ) -> dict:
        """
        Simulate a market order against the current live price.

        Behaviour mirrors Executor.submit_order() but without any API call.
        The fill price is taken from the latest ticker in DataStore.
        """
        # Kill switch check
        if self._kill_switch.is_active:
            logger.warning(
                "[DryRunExecutor] KillSwitch ACTIVE — blocking simulated entry for %s",
                signal.symbol,
            )
            return {"error": "kill_switch_active", "signal_id": signal.id}

        # Idempotency
        if signal.id in self._submitted_signals:
            logger.warning(
                "[DryRunExecutor] Signal %s already submitted — skipping duplicate.",
                signal.id,
            )
            return {"error": "duplicate_signal", "signal_id": signal.id}
        self._submitted_signals.add(signal.id)

        internal_order_id = str(uuid.uuid4())

        # Register in state machine
        regime = self._store.get_regime() or {}
        self._sm.create(
            order_id=internal_order_id,
            signal_id=signal.id,
            strategy=signal.strategy,
            regime_snapshot=regime,
        )

        side = "BUY" if signal.action == "BUY" else "SELL"

        if qty is None or qty <= 0:
            logger.error(
                "[DryRunExecutor] qty=0 for signal %s — skipping.", signal.id
            )
            self._sm.transition(internal_order_id, "REJECTED", reason="qty=0")
            return {"error": "qty_zero", "signal_id": signal.id}

        qty = round(qty, 3)

        # Get live price from DataStore
        fill_price = self._get_live_price(signal.symbol)
        if fill_price <= 0:
            logger.error(
                "[DryRunExecutor] No live price for %s — cannot simulate fill.",
                signal.symbol,
            )
            self._sm.transition(
                internal_order_id, "REJECTED", reason="no_live_price"
            )
            return {"error": "no_live_price", "signal_id": signal.id}

        # Simulate fill
        simulated_binance_id = f"DRY-{internal_order_id[:8]}"

        self._sm.transition(
            internal_order_id,
            "FILLED",
            reason=f"DryRun MARKET {side} {qty} {signal.symbol} @ {fill_price}",
        )

        # Build order record
        order_record = {
            "id": internal_order_id,
            "binance_order_id": simulated_binance_id,
            "signal_id": signal.id,
            "ts": int(time.time() * 1000),
            "symbol": signal.symbol,
            "side": side,
            "type": "MARKET",
            "qty": qty,
            "price": fill_price,
            "status": "FILLED",
            "filled_qty": qty,
            "filled_price": fill_price,
            "fee": 0.0,  # tracked in ReplayAccount
            "strategy": signal.strategy,
            "regime": signal.regime,
            "partial_fill": False,
            "dry_run": True,
        }
        self._store.save_order(order_record)
        self._simulated_orders.append(order_record)

        # Broadcast to dashboard
        self._store._broadcast("order_update", order_record)

        logger.info(
            "[DryRunExecutor] Simulated %s %s qty=%.6f @ %.4f signal=%s",
            side,
            signal.symbol,
            qty,
            fill_price,
            signal.id,
        )

        return {
            "internal_order_id": internal_order_id,
            "binance_order_id": simulated_binance_id,
            "signal_id": signal.id,
            "status": "FILLED",
            "filled_qty": qty,
            "avg_price": fill_price,
            "sl_order": None,
            "tp_order": None,
            "partial_fill": False,
            "dry_run": True,
        }

    # ------------------------------------------------------------------ #
    # Simulated queries (return empty — no real exchange positions)
    # ------------------------------------------------------------------ #

    async def get_open_positions(self) -> list:
        """Return empty — dry-run positions are tracked in PaperRecorder."""
        return []

    async def get_open_orders(self) -> list:
        """Return empty — no real orders exist."""
        return []

    async def get_account_balance(self) -> float:
        """Return the virtual account balance."""
        return self._account.balance

    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        """No-op cancel — orders are simulated instant fills."""
        logger.debug("[DryRunExecutor] cancel_order no-op for %s", order_id)
        return {"status": "DRY_RUN_CANCEL"}

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> list:
        """No-op cancel all."""
        return []

    async def close_position_reduce_only(
        self, symbol: str, side: str, qty: float
    ) -> dict:
        """No-op close — positions managed by PaperRecorder."""
        logger.debug(
            "[DryRunExecutor] close_position_reduce_only no-op for %s", symbol
        )
        return {"status": "DRY_RUN_CLOSE"}

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _get_live_price(self, symbol: str) -> float:
        """Get the latest live price from DataStore tickers."""
        ticker = self._store.get_ticker(symbol)
        if ticker:
            price = float(ticker.get("price", 0.0))
            if price > 0:
                return price
        # Fallback: last candle close
        candles = self._store.get_candles(symbol, "1h", limit=1)
        if candles:
            price = float(candles[-1].get("c", 0.0))
            if price > 0:
                return price
        return 0.0

    @property
    def simulated_orders(self) -> List[dict]:
        return list(self._simulated_orders)
