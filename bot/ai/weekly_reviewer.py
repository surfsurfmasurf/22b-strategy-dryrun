"""
Weekly Reviewer — runs every Sunday at 00:00 UTC.

Responsibilities:
  - Gather 7-day strategy performance (WR, PF, MDD, Sharpe, per-regime breakdown)
  - Use Claude to analyze patterns and generate formal recommendations
  - Store recommendations to DB with status=PENDING (require 22B approval)
  - Send Telegram weekly summary
  - Broadcast to Dashboard Panel 6

IMPORTANT:
  - Recommendations require human approval via dashboard (PENDING → APPROVED/REJECTED/DEFERRED)
  - Claude is used for analysis ONLY — recommendations are advisory, never auto-executed
  - Even if Claude is unavailable, basic stats are stored and skeleton recommendations created
"""

import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Optional, TYPE_CHECKING

from bot.ai.claude_client import ClaudeClient

if TYPE_CHECKING:
    from bot.data.store import DataStore
    from bot.notifications.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #

@dataclass
class Recommendation:
    """
    A formal strategy recommendation generated from weekly analysis.
    Follows the Part 5.3 specification exactly.
    """
    id: str
    type: str                      # PROMOTE / DEMOTE / MODIFY / RETIRE
    strategy: str
    current_mode: str              # PAPER / LIVE / PAUSED
    proposed_mode: str             # PAPER / LIVE / PAUSED / RETIRED
    supporting_data: dict          # WR, PF, slippage metrics
    counter_arguments: List[str]
    expected_risk: dict            # max_additional_exposure, correlation
    validity_days: int             # 7
    rollback_condition: str
    status: str                    # PENDING / APPROVED / REJECTED / DEFERRED
    created_at: int                # unix ms
    decided_at: Optional[int]
    decided_by: Optional[str]
    decision_reason: Optional[str]

    def to_dict(self) -> dict:
        return {
            "id":               self.id,
            "type":             self.type,
            "strategy":         self.strategy,
            "current_mode":     self.current_mode,
            "proposed_mode":    self.proposed_mode,
            "supporting_data":  self.supporting_data,
            "counter_arguments": self.counter_arguments,
            "expected_risk":    self.expected_risk,
            "validity_days":    self.validity_days,
            "rollback_condition": self.rollback_condition,
            "status":           self.status,
            "created_at":       self.created_at,
            "decided_at":       self.decided_at,
            "decided_by":       self.decided_by,
            "decision_reason":  self.decision_reason,
        }


@dataclass
class WeeklyReport:
    """Complete weekly review report."""
    id: str
    week_label: str                # e.g. "2026-W12"
    ts: int
    period_start: str              # YYYY-MM-DD
    period_end: str
    strategy_stats: dict           # name → stats dict
    regime_breakdown: dict         # regime → {trade_count, pnl_avg}
    top_performer: Optional[str]
    worst_performer: Optional[str]
    recommendations: List[Recommendation] = field(default_factory=list)
    ai_analysis: str = ""
    ai_available: bool = True

    def to_dict(self) -> dict:
        return {
            "id":               self.id,
            "week_label":       self.week_label,
            "ts":               self.ts,
            "period_start":     self.period_start,
            "period_end":       self.period_end,
            "strategy_stats":   self.strategy_stats,
            "regime_breakdown": self.regime_breakdown,
            "top_performer":    self.top_performer,
            "worst_performer":  self.worst_performer,
            "recommendations":  [r.to_dict() for r in self.recommendations],
            "ai_analysis":      self.ai_analysis,
            "ai_available":     self.ai_available,
        }


# --------------------------------------------------------------------------- #
# Weekly Reviewer
# --------------------------------------------------------------------------- #

class WeeklyReviewer:
    """
    AI-powered weekly strategy review cycle.

    Claude analyzes 7-day performance and generates actionable recommendations.
    All recommendations have status=PENDING and require 22B operator approval.
    """

    # Thresholds for recommendation triggers
    PROMOTE_PF_MIN        = 1.8    # PF >= 1.8 for PROMOTE candidate
    PROMOTE_WR_MIN        = 0.55   # WR >= 55% for PROMOTE candidate
    PROMOTE_MIN_TRADES    = 10     # minimum trades for PROMOTE
    DEMOTE_PF_MAX         = 0.8    # PF <= 0.8 for DEMOTE candidate
    DEMOTE_MIN_TRADES     = 5      # minimum trades before DEMOTE
    RETIRE_PF_MAX         = 0.5    # PF <= 0.5 consistently → RETIRE candidate

    def __init__(
        self,
        store: "DataStore",
        claude_client: ClaudeClient,
        telegram: Optional["TelegramNotifier"] = None,
    ) -> None:
        self._store = store
        self._client = claude_client
        self._telegram = telegram
        self._last_report: Optional[WeeklyReport] = None

    def get_last_report(self) -> Optional[WeeklyReport]:
        return self._last_report

    def get_last_report_dict(self) -> Optional[dict]:
        if self._last_report:
            return self._last_report.to_dict()
        return None

    async def run(self) -> WeeklyReport:
        """
        Execute the full weekly review cycle.

        Steps:
          1. Gather 7-day stats per strategy
          2. Compute per-regime breakdown
          3. Build prompt, call Claude for analysis
          4. Generate recommendations (Claude-assisted + rule-based fallback)
          5. Save recommendations to DB (status=PENDING)
          6. Store review to DB
          7. Send Telegram summary
          8. Broadcast to dashboard
        """
        ts = int(time.time() * 1000)
        now = datetime.now(timezone.utc)
        week_label = f"{now.year}-W{now.isocalendar()[1]:02d}"
        period_end = now.strftime("%Y-%m-%d")
        period_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        report_id = str(uuid.uuid4())

        logger.info("[WeeklyReviewer] Running weekly review for %s", week_label)

        # --- Gather stats ---
        strategy_stats = self._store.get_weekly_stats(days=7)
        regime_breakdown = self._get_regime_breakdown()
        top_performer, worst_performer = self._find_top_worst(strategy_stats)

        # --- AI analysis ---
        ai_available = await self._client.is_available()
        ai_analysis = ""
        if ai_available:
            prompt = self._build_weekly_prompt(strategy_stats, regime_breakdown)
            ai_analysis = await self._client.analyze_weekly(prompt)
        else:
            ai_analysis = (
                "[AI analysis unavailable — ANTHROPIC_API_KEY not configured. "
                "Rule-based recommendations generated from performance metrics.]"
            )

        # --- Generate recommendations ---
        recommendations = await self.generate_recommendations(strategy_stats, ai_analysis)

        # --- Save recommendations to DB ---
        for rec in recommendations:
            self._store.save_recommendation(rec.to_dict())

        # --- Build report ---
        report = WeeklyReport(
            id=report_id,
            week_label=week_label,
            ts=ts,
            period_start=period_start,
            period_end=period_end,
            strategy_stats=strategy_stats,
            regime_breakdown=regime_breakdown,
            top_performer=top_performer,
            worst_performer=worst_performer,
            recommendations=recommendations,
            ai_analysis=ai_analysis,
            ai_available=ai_available,
        )

        # --- Persist review ---
        self._store.save_review({
            "ts":             ts,
            "type":           "weekly",
            "content":        json.dumps(report.to_dict()),
            "recommendations": json.dumps([r.to_dict() for r in recommendations]),
        })

        # --- Telegram ---
        if self._telegram:
            msg = self._format_telegram_message(report)
            try:
                await self._telegram.send_message(msg)
            except Exception as exc:
                logger.error("[WeeklyReviewer] Telegram send failed: %s", exc)

        # --- Broadcast to dashboard ---
        self._store._broadcast("weekly_review", report.to_dict())

        self._last_report = report
        logger.info(
            "[WeeklyReviewer] Weekly review complete: %d recommendations for week %s",
            len(recommendations), week_label,
        )
        return report

    async def generate_recommendations(
        self,
        stats: dict,
        ai_analysis: str = "",
    ) -> List[Recommendation]:
        """
        Generate strategy recommendations using Claude analysis + rule-based thresholds.

        Even without AI, rule-based recommendations are produced from performance metrics.
        All recommendations start with status=PENDING and require human approval.
        """
        recommendations: List[Recommendation] = []
        ts = int(time.time() * 1000)

        for name, data in stats.items():
            n          = data.get("trade_count", 0)
            pf         = data.get("profit_factor") or 0.0
            wr         = data.get("win_rate", 0.0)
            mdd        = data.get("mdd", 0.0)
            expectancy = data.get("expectancy", 0.0)
            current_mode = data.get("mode", "PAPER")

            rec_type: Optional[str] = None
            proposed_mode: str = current_mode
            counter_args: List[str] = []
            expected_risk: dict = {"max_additional_exposure": "unchanged", "correlation": "unknown"}
            rollback_condition: str = "PF drops below 0.8 for 5+ consecutive trades"

            # PROMOTE: PAPER → LIVE candidate
            if (
                current_mode == "PAPER"
                and n >= self.PROMOTE_MIN_TRADES
                and pf >= self.PROMOTE_PF_MIN
                and wr >= self.PROMOTE_WR_MIN
                and expectancy > 0
            ):
                rec_type = "PROMOTE"
                proposed_mode = "LIVE"
                counter_args = [
                    "7 days may be insufficient to assess edge durability",
                    "Live slippage not yet measured",
                    "Regime dependency not fully characterized",
                ]
                expected_risk = {
                    "max_additional_exposure": "1-2% capital per trade",
                    "correlation": "monitor for correlation with other live strategies",
                }
                rollback_condition = f"PF drops below {self.DEMOTE_PF_MAX} over next 10 live trades"

            # DEMOTE: LIVE → PAPER candidate
            elif (
                current_mode == "LIVE"
                and n >= self.DEMOTE_MIN_TRADES
                and pf <= self.DEMOTE_PF_MAX
            ):
                rec_type = "DEMOTE"
                proposed_mode = "PAPER"
                counter_args = [
                    "Short-term underperformance may be regime-specific",
                    "Demoting may miss regime recovery",
                ]
                expected_risk = {
                    "max_additional_exposure": "reduces exposure",
                    "correlation": "n/a",
                }
                rollback_condition = "PF recovers above 1.2 over next 10 paper trades"

            # RETIRE: very poor persistent performer
            elif (
                n >= self.DEMOTE_MIN_TRADES
                and pf <= self.RETIRE_PF_MAX
                and expectancy < -0.5
            ):
                rec_type = "RETIRE"
                proposed_mode = "PAUSED"
                counter_args = [
                    "Regime shift may improve results",
                    "Parameter optimization not yet attempted",
                ]
                expected_risk = {
                    "max_additional_exposure": "reduces exposure",
                    "correlation": "n/a",
                }
                rollback_condition = "Re-evaluate if regime changes significantly"

            # MODIFY: moderate underperformer needs review
            elif (
                n >= self.DEMOTE_MIN_TRADES
                and self.DEMOTE_PF_MAX < pf < 1.0
                and current_mode in ("PAPER", "LIVE")
            ):
                rec_type = "MODIFY"
                proposed_mode = current_mode  # no mode change, just review
                counter_args = [
                    "Modifying parameters during live trading introduces new risks",
                    "Performance may recover with next regime change",
                ]
                expected_risk = {
                    "max_additional_exposure": "unchanged during review",
                    "correlation": "unknown",
                }
                rollback_condition = "Revert parameter changes if PF drops below 0.7"

            if rec_type is None:
                continue

            rec = Recommendation(
                id=str(uuid.uuid4()),
                type=rec_type,
                strategy=name,
                current_mode=current_mode,
                proposed_mode=proposed_mode,
                supporting_data={
                    "trade_count":  n,
                    "win_rate":     round(wr, 4),
                    "profit_factor": round(pf, 4),
                    "mdd":          round(mdd, 4),
                    "expectancy":   round(expectancy, 4),
                },
                counter_arguments=counter_args,
                expected_risk=expected_risk,
                validity_days=7,
                rollback_condition=rollback_condition,
                status="PENDING",
                created_at=ts,
                decided_at=None,
                decided_by=None,
                decision_reason=None,
            )
            recommendations.append(rec)

        return recommendations

    def _build_weekly_prompt(self, stats: dict, regime_breakdown: dict) -> str:
        """Build comprehensive weekly analysis prompt for Claude."""
        lines = [
            "You are a quantitative trading analyst reviewing a week of strategy performance.",
            "",
            "7-Day Strategy Performance:",
        ]

        for name, data in stats.items():
            n   = data.get("trade_count", 0)
            pf  = data.get("profit_factor") or 0.0
            wr  = (data.get("win_rate", 0.0) * 100)
            mdd = data.get("mdd", 0.0)
            exp = data.get("expectancy", 0.0)
            mode = data.get("mode", "PAPER")
            lines.append(
                f"- {name} ({mode}): {n} trades, PF={pf:.2f}, "
                f"WR={wr:.1f}%, MDD={mdd:.2f}%, Expectancy={exp:.2f}%"
            )

        if regime_breakdown:
            lines.append("")
            lines.append("Regime Distribution This Week:")
            for regime, rdata in regime_breakdown.items():
                cnt = rdata.get("count", 0)
                lines.append(f"- {regime}: {cnt} regime records")

        lines.extend([
            "",
            "Tasks:",
            "1. Identify the top performing strategy and why",
            "2. Identify the worst performing strategy and the primary failure pattern",
            "3. Note any regime-strategy mismatches (strategy active in wrong regime)",
            "4. List 2-3 concrete actionable recommendations (PROMOTE/DEMOTE/MODIFY/RETIRE)",
            "   Format each as: ACTION strategy_name: reason (supporting metric)",
            "",
            "Be concise. Total response under 400 words.",
        ])

        return "\n".join(lines)

    def _get_regime_breakdown(self) -> dict:
        """Count regime occurrences over the last 7 days."""
        since_ms = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000)
        try:
            rows = self._store._conn.execute(
                """
                SELECT regime, COUNT(*) as count
                FROM regimes
                WHERE ts >= ?
                GROUP BY regime
                ORDER BY count DESC
                """,
                (since_ms,),
            ).fetchall()
            return {row["regime"]: {"count": row["count"]} for row in rows}
        except Exception as exc:
            logger.error("[WeeklyReviewer] Regime breakdown query failed: %s", exc)
            return {}

    @staticmethod
    def _find_top_worst(stats: dict):
        """Return (top_performer_name, worst_performer_name) by profit_factor."""
        if not stats:
            return None, None

        # Only consider strategies with at least 1 trade
        eligible = {
            k: v for k, v in stats.items()
            if (v.get("trade_count", 0) or 0) > 0
        }
        if not eligible:
            return None, None

        def pf_key(item):
            pf = item[1].get("profit_factor")
            return pf if pf is not None else 0.0

        sorted_by_pf = sorted(eligible.items(), key=pf_key, reverse=True)
        top    = sorted_by_pf[0][0] if sorted_by_pf else None
        worst  = sorted_by_pf[-1][0] if len(sorted_by_pf) > 1 else None
        return top, worst

    def _format_telegram_message(self, report: WeeklyReport) -> str:
        """Format the weekly report as a readable Telegram message."""
        lines = [
            f"📈 *Weekly Review — {report.week_label}*",
            "━━━━━━━━━━━━━━━━━━━━",
            f"Period: {report.period_start} → {report.period_end}",
            "",
        ]

        # Top / Worst
        if report.top_performer:
            top_data = report.strategy_stats.get(report.top_performer, {})
            top_pf = top_data.get("profit_factor") or 0.0
            top_wr = (top_data.get("win_rate", 0.0) * 100)
            lines.append(
                f"🏆 Top performer: `{report.top_performer}` "
                f"(PF={top_pf:.2f}, WR={top_wr:.0f}%)"
            )

        if report.worst_performer:
            worst_data = report.strategy_stats.get(report.worst_performer, {})
            worst_pf = worst_data.get("profit_factor") or 0.0
            lines.append(f"📉 Worst: `{report.worst_performer}` (PF={worst_pf:.2f})")

        # Recommendations
        pending = report.recommendations
        if pending:
            lines.append("")
            lines.append(f"*AI Recommendations: {len(pending)}*")
            for i, rec in enumerate(pending, 1):
                lines.append(
                    f"{i}. {rec.type} `{rec.strategy}` "
                    f"{rec.current_mode}→{rec.proposed_mode}"
                )

        lines.extend([
            "",
            f"📊 Review on dashboard: http://localhost:8000",
            "_(All recommendations require approval)_",
        ])

        return "\n".join(lines)
