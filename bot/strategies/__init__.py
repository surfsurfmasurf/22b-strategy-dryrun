"""
bot.strategies — Phase 2 Strategy Engine

Exports:
  StrategyBase    — abstract base class all strategies must inherit
  Signal          — signal dataclass
  SignalBus       — routes signals from strategies to PaperRecorder + DataStore
  PaperRecorder   — tracks paper positions, computes WR/PF/MDD
  StrategyManager — orchestrates all strategy instances
"""

from bot.strategies._base import Signal, StrategyBase
from bot.strategies.signal_bus import SignalBus
from bot.strategies.paper_recorder import PaperRecorder
from bot.strategies.manager import StrategyManager

__all__ = [
    "Signal",
    "StrategyBase",
    "SignalBus",
    "PaperRecorder",
    "StrategyManager",
]
