"""
Phase 3 Execution Module — 22B Strategy Engine.

Components:
  - Executor        : Binance Futures order placement + lifecycle
  - RiskManager     : Pre-trade risk checks (Part 9.1)
  - OrderStateMachine: Full order/position state machine (Part 3.1)
  - Reconciler      : Position reconciliation every 5 min (Part 3.3)
  - KillSwitch      : Emergency stop + manual kill (Part 3.5)
"""

from bot.execution.executor import Executor
from bot.execution.risk_manager import RiskManager, RiskResult
from bot.execution.state_machine import OrderStateMachine
from bot.execution.reconciler import Reconciler, ReconcileResult
from bot.execution.kill_switch import KillSwitch

__all__ = [
    "Executor",
    "RiskManager",
    "RiskResult",
    "OrderStateMachine",
    "Reconciler",
    "ReconcileResult",
    "KillSwitch",
]
