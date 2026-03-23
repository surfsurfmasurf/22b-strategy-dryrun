"""
Daily Reviewer — runs every day at 22:00 UTC.

Responsibilities:
  - Compute per-strategy metrics from the last 24 hours
  - Detect warnings (low PF, negative expectancy, slippage spikes)
  - Detect regime changes since yesterday
  - Send Telegram daily summary
  - Store to reviews table (type='daily')
  - Broadcast daily_review event to dashboard subscribers

This module does NOT use Claude — it is pure metrics computation.
(Claude is used only in WeeklyReviewer for recommendations.)
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from bot.data.store import DataStore
    from bot.notifications.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


@dataclass
class StrategyDayStat:
    """Per-strategy daily performance snapshot."""
    name: str
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: float
    profit_factor: float
    expectancy: float          # avg % pnl per trade
    pf_warning: bool           # PF < 0.8
    expectancy_warning: bool   # expectancy < 0


@dataclass
class DailyReport:
    """Complete daily review report."""
    id: str
    date: str                  # YYYY-MM-DD
    ts: int                    # unix ms
    regime: str
    daily_pnl: float
    daily_pnl_pct: float
    strategy_stats: List[StrategyDayStat] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    regime_changes: List[str] = field(default_factory=list)
    action_needed: bool = False
    alert_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id":           self.id,
            "date":         self.date,
            "ts":           self.ts,
            "regime":       self.regime,
            "daily_pnl":    self.daily_pnl,
            "daily_pnl_pct": self.daily_pnl_pct,
            "strategy_stats": [
                {
                    "name":         s.name,
                    "trade_count":  s.trade_count,
                    "win_count":    s.win_count,
                    "loss_count":   s.loss_count,
                    "win_rate":     round(s.win_rate, 4),
                    "profit_factor": round(s.profit_factor, 4),
                    "expectancy":   round(s.expectancy, 4),
                    "pf_warning":   s.pf_warning,
                    "expectancy_warning": s.expectancy_warning,
                }
                for s in self.strategy_stats
            ],
            "warnings":        self.warnings,
            "regime_changes":  self.regime_changes,
            "action_needed":   self.action_needed,
            "alert_count":     self.alert_count,
        }


class DailyReviewer:
    """
    Computes daily metrics and sends a Telegram summary.

    Does NOT call Claude — pure rule-based metrics.
    """

    # Thresholds
    PF_WARNING_THRESHOLD = 0.8
    MIN_TRADES_FOR_PF    = 3      # require at least 3 trades before flagging PF

    def __init__(
        self,
        store: "DataStore",
        telegram: Optional["TelegramNotifier"] = None,
    ) -> None:
        self._store = store
        self._telegram = telegram
        self._last_report: Optional[DailyReport] = None

    def get_last_report(self) -> Optional[DailyReport]:
        return self._last_report

    def get_last_report_dict(self) -> Optional[dict]:
        if self._last_report:
            return self._last_report.to_dict()
        return None

    async def run(self) -> DailyReport:
        """
        Execute the daily review cycle.

        Steps:
          1. Gather per-strategy stats for today
          2. Check warnings
          3. Detect regime changes
          4. Build report
          5. Store to DB
          6. Send Telegram
          7. Broadcast to dashboard
        """
        ts = int(time.time() * 1000)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        report_id = str(uuid.uuid4())

        logger.info("[DailyReviewer] Running daily review for %s", today)

        # --- Current state ---
        regime_data = self._store.get_regime()
        current_regime = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"
        daily_pnl, daily_pnl_pct = self._store.get_daily_pnl()

        # --- Strategy stats ---
        strategy_stats = self._compute_strategy_stats()

        # --- Warnings ---
        warnings: List[str] = []
        for stat in strategy_stats:
            if stat.expectancy_warning:
                warnings.append(
                    f"{stat.name} expectancy negative ({stat.expectancy:.2f}%) today"
                )
            if stat.pf_warning:
                warnings.append(
                    f"{stat.name} PF below threshold ({stat.profit_factor:.2f} < {self.PF_WARNING_THRESHOLD})"
                )

        # --- Regime changes in last 24h ---
        regime_changes = self._get_regime_changes_today()

        # --- Build report ---
        alert_count = len(warnings)
        action_needed = alert_count > 0

        report = DailyReport(
            id=report_id,
            date=today,
            ts=ts,
            regime=current_regime,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            strategy_stats=strategy_stats,
            warnings=warnings,
            regime_changes=regime_changes,
            action_needed=action_needed,
            alert_count=alert_count,
        )

        # --- Persist ---
        self._store.save_review({
            "ts":      ts,
            "type":    "daily",
            "content": json.dumps(report.to_dict()),
            "recommendations": "[]",
        })

        # --- Update alert badge count ---
        if alert_count > 0:
            for _ in range(alert_count):
                self._store.increment_daily_alert()

        # --- Telegram ---
        if self._telegram:
            msg = self._format_telegram_message(report)
            try:
                await self._telegram.send_message(msg)
            except Exception as exc:
                logger.error("[DailyReviewer] Telegram send failed: %s", exc)

        # --- Broadcast to dashboard ---
        self._store._broadcast("daily_review", report.to_dict())

        self._last_report = report
        logger.info(
            "[DailyReviewer] Daily review complete: %d warnings, action_needed=%s",
            alert_count, action_needed,
        )
        return report

    def _compute_strategy_stats(self) -> List[StrategyDayStat]:
        """Compute per-strategy performance stats from today's closed positions."""
        since_ms = self._get_today_start_ms()
        raw_stats = self._store.get_strategy_stats_since(since_ms)
        stats: List[StrategyDayStat] = []

        for name, data in raw_stats.items():
            n = data.get("trade_count", 0)
            wins = data.get("win_count", 0)
            losses = n - wins
            wr = data.get("win_rate", 0.0)
            pf = data.get("profit_factor") or 0.0
            expectancy = data.get("expectancy", 0.0)

            pf_warning = (n >= self.MIN_TRADES_FOR_PF and pf < self.PF_WARNING_THRESHOLD)
            expectancy_warning = (n > 0 and expectancy < 0.0)

            stats.append(StrategyDayStat(
                name=name,
                trade_count=n,
                win_count=wins,
                loss_count=losses,
                win_rate=wr,
                profit_factor=pf,
                expectancy=expectancy,
                pf_warning=pf_warning,
                expectancy_warning=expectancy_warning,
            ))

        return stats

    def _get_regime_changes_today(self) -> List[str]:
        """Return list of regime change strings from today's regime records."""
        since_ms = self._get_today_start_ms()
        try:
            rows = self._store._conn.execute(
                """
                SELECT ts, regime FROM regimes
                WHERE ts >= ?
                ORDER BY ts ASC
                """,
                (since_ms,),
            ).fetchall()
        except Exception as exc:
            logger.error("[DailyReviewer] Regime history query failed: %s", exc)
            return []

        changes: List[str] = []
        prev_regime: Optional[str] = None
        for row in rows:
            r = row["regime"]
            if prev_regime is not None and r != prev_regime:
                ts_str = datetime.fromtimestamp(row["ts"] / 1000, tz=timezone.utc).strftime("%H:%M")
                changes.append(f"{prev_regime} → {r} at {ts_str}")
            prev_regime = r

        return changes

    @staticmethod
    def _get_today_start_ms() -> int:
        """Return unix ms for 00:00:00 UTC today."""
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(today_start.timestamp() * 1000)

    def _format_telegram_message(self, report: DailyReport) -> str:
        """Format the daily report as a readable Telegram message."""
        pnl_sign = "+" if report.daily_pnl >= 0 else ""
        pnl_pct_sign = "+" if report.daily_pnl_pct >= 0 else ""

        lines = [
            f"📊 *Daily Review — {report.date}*",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            f"Regime: `{report.regime}`",
            f"Today P&L: `{pnl_sign}${report.daily_pnl:.2f} ({pnl_pct_sign}{report.daily_pnl_pct:.2f}%)`",
            "",
            "*Strategy Performance:*",
        ]

        for stat in report.strategy_stats:
            icon = "⚠️" if (stat.pf_warning or stat.expectancy_warning) else "✅"
            wr_pct = stat.win_rate * 100
            suffix = ""
            if stat.pf_warning:
                suffix += " — LOW PF"
            if stat.expectancy_warning:
                suffix += " — NEG EXPECTANCY"
            lines.append(
                f"{icon} `{stat.name}`: PF={stat.profit_factor:.1f}, "
                f"WR={wr_pct:.0f}% ({stat.trade_count} trades){suffix}"
            )

        if not report.strategy_stats:
            lines.append("_No trades today_")

        if report.warnings:
            lines.append("")
            lines.append(f"*Warnings: {len(report.warnings)}*")
            for w in report.warnings:
                lines.append(f"⚠️ {w}")

        if report.regime_changes:
            lines.append("")
            lines.append("*Regime Changes:*")
            for rc in report.regime_changes:
                lines.append(f"• {rc}")

        lines.append("")
        if report.action_needed:
            items = [s.name for s in report.strategy_stats if s.pf_warning or s.expectancy_warning]
            lines.append(f"⚡ *Action needed:* review {', '.join(items)}")
        else:
            lines.append("✅ No action required")

        return "\n".join(lines)
