"""
BacktestReporter — generates a structured backtest result report from ReplayAccount.

Output:
  - JSON report dict (stored to data/backtest_reports/)
  - Telegram summary message
  - Returns report dict for /api/backtest-report endpoint
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from bot.data.replay_account import ReplayAccount
    from bot.notification.telegram import TelegramClient

logger = logging.getLogger(__name__)

REPORTS_DIR = "./data/backtest_reports"


class BacktestReporter:
    """
    Builds and persists a backtest performance report.

    Usage
    -----
    reporter = BacktestReporter(account, telegram=telegram_client)
    report = reporter.generate(label="btc_1h_2024")
    """

    def __init__(
        self,
        account: "ReplayAccount",
        telegram: Optional["TelegramClient"] = None,
    ) -> None:
        self._account = account
        self._telegram = telegram

    def generate(self, label: str = "") -> dict:
        """
        Generate report, persist to disk, and send Telegram summary.
        Returns the full report dict.
        """
        metrics = self._account.compute_metrics()
        equity  = self._account.equity_curve

        now_utc = datetime.now(timezone.utc)
        report = {
            "label":       label or now_utc.strftime("%Y%m%d_%H%M%S"),
            "generated_at": now_utc.isoformat(),
            "metrics":     metrics,
            "equity_curve": [
                {"ts_ms": ts, "balance": round(bal, 4)}
                for ts, bal in equity
            ],
        }

        self._save_report(report)

        if self._telegram is not None:
            self._send_telegram_summary(metrics, label)

        logger.info(
            "[BacktestReporter] Report generated: trades=%d  return=%.2f%%  mdd=%.2f%%",
            metrics.get("trade_count", 0),
            metrics.get("total_return_pct", 0.0),
            metrics.get("mdd_pct", 0.0),
        )
        return report

    # ---------------------------------------------------------------------- #
    # Internal helpers
    # ---------------------------------------------------------------------- #

    def _save_report(self, report: dict) -> None:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        filename = f"{REPORTS_DIR}/backtest_{report['label']}.json"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info("[BacktestReporter] Saved to %s", filename)
        except Exception as exc:
            logger.error("[BacktestReporter] Failed to save report: %s", exc)

    def _send_telegram_summary(self, metrics: dict, label: str) -> None:
        if metrics.get("trade_count", 0) == 0:
            msg = f"📊 *Backtest {label}*\n거래 없음"
        else:
            pf = metrics.get("profit_factor")
            pf_str = f"{pf:.2f}" if pf is not None else "∞"
            win_rate_pct = metrics.get("win_rate", 0) * 100

            lines = [
                f"📊 *Backtest Report* `{label}`",
                f"거래 횟수: {metrics['trade_count']}  (승률 {win_rate_pct:.1f}%)",
                f"총 수익: `{metrics['total_return_pct']:+.2f}%` ({metrics['total_pnl_usdt']:+.2f} USDT)",
                f"잔고: {metrics['initial_balance']:.0f} → {metrics['final_balance']:.2f} USDT",
                f"MDD: `{metrics['mdd_pct']:.2f}%`  Sharpe: {metrics['sharpe_ratio']:.2f}",
                f"Profit Factor: {pf_str}  Expectancy: {metrics['expectancy_usdt']:.2f} USDT",
                f"수수료 합계: {metrics['total_fee_usdt']:.4f} USDT",
                f"슬리피지 합계: {metrics['total_slippage_usdt']:.4f} USDT",
                f"평균 보유 시간: {metrics['avg_duration_hours']:.1f}h",
            ]

            per_strat = metrics.get("per_strategy", {})
            if per_strat:
                lines.append("\n*전략별 성과:*")
                for sname, sd in per_strat.items():
                    pf2 = sd.get("profit_factor")
                    pf2_str = f"{pf2:.2f}" if pf2 is not None else "∞"
                    lines.append(
                        f"  `{sname}`: {sd['trade_count']}건  "
                        f"승률{sd['win_rate']*100:.0f}%  "
                        f"PnL {sd['total_pnl_usdt']:+.2f}  PF {pf2_str}"
                    )

            msg = "\n".join(lines)

        try:
            self._telegram.notify(msg)
        except Exception as exc:
            logger.warning("[BacktestReporter] Telegram send failed: %s", exc)


def load_latest_report() -> Optional[dict]:
    """Load the most recently saved backtest report from disk."""
    if not os.path.isdir(REPORTS_DIR):
        return None
    files = sorted(
        [f for f in os.listdir(REPORTS_DIR) if f.endswith(".json")],
        reverse=True,
    )
    if not files:
        return None
    path = os.path.join(REPORTS_DIR, files[0])
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error("[BacktestReporter] Failed to load report %s: %s", path, exc)
        return None
