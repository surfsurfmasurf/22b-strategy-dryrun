"""
Regime Interpreter — uses Claude to explain the current market regime.

After the RegimeDetector classifies a regime, this module asks Claude:
  1. Why is this regime (3 key factors)
  2. What signals to watch for the next regime transition
  3. Recommended strategy category adjustments

Output is stored in-memory and served via Dashboard Panel 6.
IMPORTANT: This is analysis only — never execution.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from bot.ai.claude_client import ClaudeClient

logger = logging.getLogger(__name__)


@dataclass
class RegimeInterpretation:
    """Structured result from Claude's regime analysis."""

    regime: str
    timestamp: int                   # unix ms when interpretation was generated
    raw_response: str                # full Claude response text
    why_factors: list                # extracted key factors (up to 3 bullet strings)
    transition_signals: str          # what to watch for next transition
    strategy_recommendations: str    # strategy category suggestions
    regime_snapshot: dict = field(default_factory=dict)   # indicators at time of analysis
    ai_available: bool = True

    def to_dict(self) -> dict:
        return {
            "regime":                  self.regime,
            "timestamp":               self.timestamp,
            "raw_response":            self.raw_response,
            "why_factors":             self.why_factors,
            "transition_signals":      self.transition_signals,
            "strategy_recommendations": self.strategy_recommendations,
            "regime_snapshot":         self.regime_snapshot,
            "ai_available":            self.ai_available,
        }


class RegimeInterpreter:
    """
    Interprets the current regime using Claude.

    Usage
    -----
    interpreter = RegimeInterpreter(claude_client)
    interp = await interpreter.interpret(regime_result)
    """

    def __init__(self, claude_client: ClaudeClient) -> None:
        self._client = claude_client
        self._last_interpretation: Optional[RegimeInterpretation] = None

    def get_last_interpretation(self) -> Optional[RegimeInterpretation]:
        """Return the most recent interpretation (or None if never run)."""
        return self._last_interpretation

    def get_last_interpretation_dict(self) -> Optional[dict]:
        if self._last_interpretation:
            return self._last_interpretation.to_dict()
        return None

    async def interpret(self, regime_result: dict) -> RegimeInterpretation:
        """
        Given a regime_result dict from RegimeDetector.detect(), ask Claude
        to interpret the regime and return a structured RegimeInterpretation.

        Falls back gracefully if AI is unavailable.
        """
        regime = regime_result.get("regime", "UNKNOWN")
        ts = int(time.time() * 1000)

        ai_available = await self._client.is_available()

        if not ai_available:
            interp = RegimeInterpretation(
                regime=regime,
                timestamp=ts,
                raw_response="[AI analysis unavailable]",
                why_factors=["AI not configured", "Check ANTHROPIC_API_KEY", "Set AI_ENABLED=true"],
                transition_signals="AI analysis unavailable — check configuration.",
                strategy_recommendations="Refer to REGIME_ALLOW_TABLE for strategy guidance.",
                regime_snapshot=regime_result,
                ai_available=False,
            )
            self._last_interpretation = interp
            return interp

        prompt = self.build_prompt(regime_result)

        try:
            raw = await self._client.analyze_regime(prompt)
            interp = self._parse_response(regime, ts, raw, regime_result)
        except Exception as exc:
            logger.error("RegimeInterpreter.interpret failed: %s", exc)
            interp = RegimeInterpretation(
                regime=regime,
                timestamp=ts,
                raw_response=f"[Error during analysis: {exc}]",
                why_factors=["Analysis error — see logs"],
                transition_signals="Analysis unavailable due to error.",
                strategy_recommendations="Use default regime strategy table.",
                regime_snapshot=regime_result,
                ai_available=True,
            )

        self._last_interpretation = interp
        logger.info(
            "RegimeInterpreter: regime=%s interpreted at %d",
            regime, ts,
        )
        return interp

    def build_prompt(self, regime_result: dict) -> str:
        """Build the analysis prompt with indicator data from regime_result."""
        regime    = regime_result.get("regime", "UNKNOWN")
        price     = regime_result.get("btc_price", "N/A")
        ema50     = regime_result.get("btc_ema50", "N/A")
        atr_pct   = regime_result.get("btc_atr_pct", "N/A")
        ret_24h   = regime_result.get("btc_ret_24h", "N/A")
        ret_1h    = regime_result.get("btc_ret_1h", "N/A")
        funding   = regime_result.get("funding", "N/A")
        rsi       = regime_result.get("btc_rsi", "N/A")

        # Determine price vs EMA50 relation
        if isinstance(price, (int, float)) and isinstance(ema50, (int, float)):
            ema_relation = "above" if price > ema50 else "below"
        else:
            ema_relation = "N/A"

        # Format numeric values
        def fmt(v, decimals=2):
            if isinstance(v, float):
                return f"{v:.{decimals}f}"
            return str(v)

        prompt = f"""You are a quantitative trading analyst. Analyze the current market regime.

Current regime: {regime}
Indicators:
- BTC Price: {fmt(price, 2)}
- EMA50: {fmt(ema50, 2)} (price is {ema_relation} EMA50)
- ATR%: {fmt(atr_pct, 2)}% (volatility)
- 24H Return: {fmt(ret_24h, 2)}%
- 1H Return: {fmt(ret_1h, 2)}%
- Funding Rate: {fmt(funding, 4)}%
- RSI(14): {fmt(rsi, 1)}

Provide:
1. Why this regime (3 key factors, concise)
2. Next regime transition signals to watch
3. Strategy category recommendations for this regime

Be concise. Total response under 300 words."""

        return prompt

    def _parse_response(
        self,
        regime: str,
        ts: int,
        raw: str,
        regime_snapshot: dict,
    ) -> RegimeInterpretation:
        """
        Parse Claude's free-text response into structured fields.

        Looks for numbered sections (1., 2., 3.) and extracts them.
        Falls back to splitting the raw text into approximate thirds.
        """
        why_factors: list = []
        transition_signals: str = ""
        strategy_recommendations: str = ""

        try:
            lines = raw.strip().split("\n")
            sections: dict = {1: [], 2: [], 3: []}
            current_section = 0

            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("1.") or stripped.lower().startswith("1."):
                    current_section = 1
                    content = stripped[2:].strip()
                    if content:
                        sections[1].append(content)
                elif stripped.startswith("2.") or stripped.lower().startswith("2."):
                    current_section = 2
                    content = stripped[2:].strip()
                    if content:
                        sections[2].append(content)
                elif stripped.startswith("3.") or stripped.lower().startswith("3."):
                    current_section = 3
                    content = stripped[2:].strip()
                    if content:
                        sections[3].append(content)
                elif current_section > 0:
                    sections[current_section].append(stripped)

            # Section 1 → why_factors (extract bullet points or lines)
            section1_text = " ".join(sections[1])
            # Try to find dash-prefixed bullets
            raw_bullets = [
                l.strip().lstrip("-").lstrip("*").lstrip("•").strip()
                for l in sections[1]
                if l.strip().startswith(("-", "*", "•")) or (
                    current_section == 1 and l.strip()
                )
            ]
            if raw_bullets:
                why_factors = raw_bullets[:3]
            elif section1_text:
                # Split by sentence endings or commas
                parts = [p.strip() for p in section1_text.replace(";", ",").split(",") if p.strip()]
                why_factors = parts[:3] if parts else [section1_text[:120]]
            else:
                why_factors = ["See raw AI response below"]

            transition_signals = " ".join(sections[2]) if sections[2] else "See raw response."
            strategy_recommendations = " ".join(sections[3]) if sections[3] else "See raw response."

        except Exception as exc:
            logger.warning("RegimeInterpreter._parse_response error: %s", exc)
            why_factors = ["Parse error — see raw response"]
            transition_signals = raw[:200]
            strategy_recommendations = ""

        return RegimeInterpretation(
            regime=regime,
            timestamp=ts,
            raw_response=raw,
            why_factors=why_factors,
            transition_signals=transition_signals,
            strategy_recommendations=strategy_recommendations,
            regime_snapshot=regime_snapshot,
            ai_available=True,
        )
