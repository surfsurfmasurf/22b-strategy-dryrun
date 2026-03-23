"""
KillSwitch — Emergency stop for the 22B Strategy Engine (Part 3.5).

Auto-triggers on:
  - Daily loss limit exceeded
  - 3 consecutive API failures
  - Reconciliation discrepancy
  - Unexpected position found

Manual triggers:
  - Dashboard KILL SWITCH button  (POST /api/kill-switch)
  - Telegram /kill command
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from bot.data.store import DataStore
    from bot.notifications.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


class KillSwitch:
    """
    Synchronous flag-based kill switch with async action execution.

    The `is_active` property is checked synchronously before every order
    submission so there is zero latency overhead.

    The `trigger()` coroutine performs all cleanup actions:
      1. Sets the BLOCKED flag immediately (synchronous)
      2. Cancels all open orders on Binance (async)
      3. Updates system mode → BLOCKED in DataStore
      4. Sends urgent Telegram alert
    """

    def __init__(
        self,
        store: "DataStore",
        telegram: Optional["TelegramNotifier"] = None,
    ) -> None:
        self._store = store
        self._telegram = telegram
        self._active: bool = False
        self._reason: str = ""
        self._triggered_at: Optional[int] = None
        self._triggered_by: Optional[str] = None
        self._reset_by: Optional[str] = None
        self._reset_at: Optional[int] = None

        # Reference to executor is injected after construction to avoid circular deps
        self._executor = None

    def set_executor(self, executor) -> None:
        """Inject Executor reference (called by Engine after both are created)."""
        self._executor = executor

    # ---------------------------------------------------------------------- #
    # Core flag
    # ---------------------------------------------------------------------- #

    @property
    def is_active(self) -> bool:
        """Synchronous check — called before EVERY order submission."""
        return self._active

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def triggered_at(self) -> Optional[int]:
        return self._triggered_at

    # ---------------------------------------------------------------------- #
    # Trigger
    # ---------------------------------------------------------------------- #

    async def trigger(self, reason: str, triggered_by: str = "system") -> None:
        """
        Activate the kill switch.

        Steps:
          1. Set BLOCKED flag immediately (synchronous — no await)
          2. Cancel all open orders on Binance
          3. Keep existing positions (SL/TP protect them)
          4. Set system mode → BLOCKED in DataStore
          5. Send urgent Telegram alert
        """
        if self._active:
            logger.warning(
                "[KillSwitch] Already active (reason=%s). New trigger ignored: %s",
                self._reason, reason,
            )
            return

        # Step 1: Block immediately (synchronous)
        self._active = True
        self._reason = reason
        self._triggered_at = int(time.time() * 1000)
        self._triggered_by = triggered_by

        logger.critical(
            "[KillSwitch] TRIGGERED — reason='%s' by='%s'",
            reason, triggered_by,
        )

        # Step 2: Cancel all open orders (best-effort — do not let failure prevent mode change)
        if self._executor is not None:
            try:
                cancelled = await self._executor.cancel_all_orders()
                logger.warning(
                    "[KillSwitch] Cancelled %d open orders.", len(cancelled)
                )
            except Exception as exc:
                logger.error(
                    "[KillSwitch] Failed to cancel orders during kill: %s", exc
                )

        # Step 3: Positions are kept — SL orders protect them.

        # Step 4: Update system mode → BLOCKED
        self._store.set_system_mode("BLOCKED")
        self._store._broadcast("kill_switch", {
            "active": True,
            "reason": reason,
            "triggered_by": triggered_by,
            "ts": self._triggered_at,
        })

        # Step 5: Telegram alert
        if self._telegram is not None:
            self._telegram.notify_kill_switch(reason)

    # ---------------------------------------------------------------------- #
    # Reset
    # ---------------------------------------------------------------------- #

    def reset(self, authorized_by: str) -> None:
        """
        Reset the kill switch — allows new entries again.

        This is a manual operation requiring explicit authorization.
        The system mode is set back to OBSERVE (safest default).
        The operator must manually change to ACTIVE/LIMITED as appropriate.
        """
        if not self._active:
            logger.info("[KillSwitch] Reset called but switch is not active.")
            return

        logger.warning(
            "[KillSwitch] RESET by='%s' (previous reason='%s')",
            authorized_by, self._reason,
        )

        self._active = False
        self._reset_by = authorized_by
        self._reset_at = int(time.time() * 1000)

        # Restore to OBSERVE (safe default — operator promotes manually)
        self._store.set_system_mode("OBSERVE")
        self._store._broadcast("kill_switch", {
            "active": False,
            "reset_by": authorized_by,
            "ts": self._reset_at,
        })

        if self._telegram is not None:
            self._telegram._enqueue(
                f"*Kill Switch RESET*\n"
                f"Reset by: `{authorized_by}`\n"
                f"Previous reason: {self._reason}\n"
                f"System mode → OBSERVE\n"
                f"Time: {_ts()}"
            )

    # ---------------------------------------------------------------------- #
    # Status dict (for dashboard)
    # ---------------------------------------------------------------------- #

    def get_status(self) -> dict:
        return {
            "active": self._active,
            "reason": self._reason,
            "triggered_at": self._triggered_at,
            "triggered_by": self._triggered_by,
            "reset_by": self._reset_by,
            "reset_at": self._reset_at,
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _ts() -> str:
    import datetime
    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
