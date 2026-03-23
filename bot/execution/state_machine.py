"""
OrderStateMachine — Full order/position lifecycle state machine (Part 3.1).

State flow:
  SIGNAL_CREATED → RISK_CHECKED → ORDER_SUBMITTED → PARTIALLY_FILLED/FILLED/REJECTED
  → SL_ATTACHED → TP_ATTACHED → MONITORING
  → TP_HIT / SL_HIT / TRAILING_HIT / MANUAL_CLOSE / TIME_EXIT
  → CLOSED → RECONCILED

Every state transition is recorded in the audit_trail table (immutable — INSERT only).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from bot.data.store import DataStore

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Valid transition map
# --------------------------------------------------------------------------- #

TRANSITIONS: Dict[str, List[str]] = {
    "SIGNAL_CREATED":   ["RISK_CHECKED"],
    "RISK_CHECKED":     ["ORDER_SUBMITTED", "REJECTED"],
    "ORDER_SUBMITTED":  ["PARTIALLY_FILLED", "FILLED", "REJECTED"],
    "PARTIALLY_FILLED": ["FILLED", "SL_ATTACHED"],
    "FILLED":           ["SL_ATTACHED"],
    "SL_ATTACHED":      ["TP_ATTACHED", "MONITORING"],
    "TP_ATTACHED":      ["MONITORING"],
    "MONITORING":       ["TP_HIT", "SL_HIT", "TRAILING_HIT", "MANUAL_CLOSE", "TIME_EXIT"],
    "TP_HIT":           ["CLOSED"],
    "SL_HIT":           ["CLOSED"],
    "TRAILING_HIT":     ["CLOSED"],
    "MANUAL_CLOSE":     ["CLOSED"],
    "TIME_EXIT":        ["CLOSED"],
    "CLOSED":           ["RECONCILED"],
    "REJECTED":         [],
    "RECONCILED":       [],
}

# Terminal states — no further transitions allowed
TERMINAL_STATES = {"REJECTED", "RECONCILED"}


class OrderStateMachine:
    """
    Manages the lifecycle state of orders/positions.

    State is held in memory (fast path) and persisted to:
      - orders table (current status field)
      - audit_trail table (each transition as an immutable INSERT)

    Usage
    -----
    sm = OrderStateMachine(store)
    sm.create(order_id, signal_id, strategy, regime_snapshot)
    sm.transition(order_id, "RISK_CHECKED", reason="Risk passed: 1% per trade")
    sm.transition(order_id, "ORDER_SUBMITTED", reason="Binance order_id=12345")
    """

    def __init__(self, store: "DataStore") -> None:
        self._store = store
        # In-memory state: order_id → current status string
        self._states: Dict[str, str] = {}
        # In-memory metadata per order
        self._meta: Dict[str, dict] = {}

    # ---------------------------------------------------------------------- #
    # Create initial state
    # ---------------------------------------------------------------------- #

    def create(
        self,
        order_id: str,
        signal_id: str,
        strategy: str,
        regime_snapshot: dict,
        extra_meta: Optional[dict] = None,
    ) -> None:
        """
        Register a new order in SIGNAL_CREATED state.
        Records initial audit trail entry.
        """
        if order_id in self._states:
            logger.warning(
                "[StateMachine] order_id=%s already registered (state=%s). Skipping create.",
                order_id, self._states[order_id],
            )
            return

        self._states[order_id] = "SIGNAL_CREATED"
        self._meta[order_id] = {
            "signal_id": signal_id,
            "strategy": strategy,
            "regime_snapshot": regime_snapshot,
            "created_at": int(time.time() * 1000),
        }
        if extra_meta:
            self._meta[order_id].update(extra_meta)

        # Audit trail — initial entry
        self._store.save_audit_trail({
            "order_id": order_id,
            "signal_id": signal_id,
            "strategy": strategy,
            "from_status": None,
            "to_status": "SIGNAL_CREATED",
            "regime_snapshot": regime_snapshot,
            "risk_check_result": None,
            "order_params": None,
            "execution_result": None,
            "decision_reason": "Signal created",
            "ts": int(time.time() * 1000),
        })

        logger.debug(
            "[StateMachine] order_id=%s created: state=SIGNAL_CREATED strategy=%s",
            order_id, strategy,
        )

    # ---------------------------------------------------------------------- #
    # Transition
    # ---------------------------------------------------------------------- #

    def transition(
        self,
        order_id: str,
        new_status: str,
        reason: str = "",
        risk_check_result: Optional[dict] = None,
        order_params: Optional[dict] = None,
        execution_result: Optional[dict] = None,
    ) -> bool:
        """
        Transition order to new_status.

        Returns True on success, False if transition is invalid.
        Records every transition to audit_trail (immutable INSERT).
        """
        if order_id not in self._states:
            logger.error(
                "[StateMachine] transition() called for unknown order_id=%s", order_id
            )
            return False

        current = self._states[order_id]

        if current in TERMINAL_STATES:
            logger.warning(
                "[StateMachine] order_id=%s is in terminal state '%s'. Cannot transition to '%s'.",
                order_id, current, new_status,
            )
            return False

        allowed = TRANSITIONS.get(current, [])
        if new_status not in allowed:
            logger.error(
                "[StateMachine] ILLEGAL transition: order_id=%s %s → %s (allowed: %s)",
                order_id, current, new_status, allowed,
            )
            return False

        # Perform transition
        self._states[order_id] = new_status
        ts = int(time.time() * 1000)

        meta = self._meta.get(order_id, {})

        # Persist to audit_trail (INSERT only — immutable)
        self._store.save_audit_trail({
            "order_id": order_id,
            "signal_id": meta.get("signal_id"),
            "strategy": meta.get("strategy"),
            "from_status": current,
            "to_status": new_status,
            "regime_snapshot": meta.get("regime_snapshot"),
            "risk_check_result": risk_check_result,
            "order_params": order_params,
            "execution_result": execution_result,
            "decision_reason": reason,
            "ts": ts,
        })

        # Update orders table status field
        self._store.update_order(order_id, {"status": new_status})

        logger.info(
            "[StateMachine] order_id=%s: %s → %s | %s",
            order_id, current, new_status, reason,
        )
        return True

    # ---------------------------------------------------------------------- #
    # Getters
    # ---------------------------------------------------------------------- #

    def get_state(self, order_id: str) -> Optional[str]:
        """Return current state string, or None if order unknown."""
        return self._states.get(order_id)

    def is_terminal(self, order_id: str) -> bool:
        state = self._states.get(order_id)
        return state in TERMINAL_STATES if state else False

    def get_all_active(self) -> Dict[str, str]:
        """Return {order_id: state} for all non-terminal orders."""
        return {
            oid: s for oid, s in self._states.items()
            if s not in TERMINAL_STATES
        }

    def get_orders_in_state(self, status: str) -> List[str]:
        """Return list of order_ids currently in the given state."""
        return [oid for oid, s in self._states.items() if s == status]

    def load_from_db(self) -> None:
        """
        Warm the in-memory state dict from the DB on startup.
        Loads all non-terminal orders from the orders table.
        """
        try:
            rows = self._store._conn.execute(
                """
                SELECT id, status, signal_id, strategy
                FROM orders
                WHERE status NOT IN ('REJECTED', 'RECONCILED', 'CANCELLED', 'FAILED')
                """
            ).fetchall()
            for row in rows:
                order_id = str(row["id"]) if row["id"] else None
                if order_id:
                    status = row["status"] or "ORDER_SUBMITTED"
                    # Map old DB statuses to state machine statuses
                    status = _map_legacy_status(status)
                    self._states[order_id] = status
                    self._meta[order_id] = {
                        "signal_id": row["signal_id"],
                        "strategy": row["strategy"] if "strategy" in row.keys() else "",
                        "regime_snapshot": {},
                        "created_at": 0,
                    }
            logger.info(
                "[StateMachine] Loaded %d active orders from DB on startup.", len(rows)
            )
        except Exception as exc:
            logger.error("[StateMachine] load_from_db error: %s", exc)


def _map_legacy_status(status: str) -> str:
    """Map database status strings to state machine status strings."""
    mapping = {
        "PENDING": "ORDER_SUBMITTED",
        "OPEN": "MONITORING",
        "FILLED": "FILLED",
        "CANCELLED": "CLOSED",
        "FAILED": "REJECTED",
    }
    return mapping.get(status, status)
