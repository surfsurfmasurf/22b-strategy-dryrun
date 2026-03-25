"""
AI client for the 22B Strategy Engine — Phase 4.

Supports two backends (auto-selected):
  1. Anthropic Claude API direct  (preferred — ANTHROPIC_API_KEY)
  2. OpenClaw proxy               (fallback  — OPENCLAW_BASE_URL)

IMPORTANT:
  - AI is NEVER used for execution decisions.
  - Used ONLY for: analysis, interpretation, recommendations.
  - All trade execution remains deterministic and rule-based.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

MAX_TOKENS_REGIME = 2000
MAX_TOKENS_WEEKLY = 4000
MAX_TOKENS_DAILY  = 1500

DEFAULT_SYSTEM = (
    "You are a quantitative trading analyst assistant for the 22B Strategy Engine. "
    "Your role is analysis and interpretation only. You never make execution decisions. "
    "Be concise, data-driven, and actionable in your responses."
)


class ClaudeClient:
    """
    AI client with automatic backend selection.

    Priority:
      1. ANTHROPIC_API_KEY set → use anthropic SDK directly
      2. OPENCLAW_BASE_URL set → use OpenAI-compatible proxy
      3. Neither → graceful degradation (is_available = False)
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "claude-sonnet-4-20250514",
        ai_enabled: bool = True,
        # Legacy OpenClaw params (backward compat)
        base_url: str = "",
        token: str = "",
        agent_id: str = "main",
    ) -> None:
        self._ai_enabled = ai_enabled
        self._model = model
        self._backend = "none"  # "anthropic" | "openclaw" | "none"

        # SDK clients
        self._anthropic_client = None
        self._openai_client = None
        self._agent_id = agent_id

        if not self._ai_enabled:
            logger.info("AI_ENABLED=false — AI features disabled")
            return

        # --- Try Anthropic SDK first ---
        if api_key:
            try:
                import anthropic
                self._anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)
                self._backend = "anthropic"
                logger.info(
                    "ClaudeClient initialized — backend=Anthropic API, model=%s",
                    self._model,
                )
                return
            except ImportError:
                logger.warning(
                    "anthropic package not installed. Run: pip install anthropic"
                )
            except Exception as exc:
                logger.warning("Anthropic SDK init failed: %s", exc)

        # --- Fallback: OpenClaw proxy ---
        if base_url:
            try:
                from openai import AsyncOpenAI
                self._openai_client = AsyncOpenAI(
                    api_key=token or "no-key",
                    base_url=f"{base_url.rstrip('/')}/v1",
                )
                self._backend = "openclaw"
                logger.info(
                    "ClaudeClient initialized — backend=OpenClaw, base=%s agent=%s",
                    base_url, agent_id,
                )
                return
            except ImportError:
                logger.warning(
                    "openai package not installed for OpenClaw fallback. Run: pip install openai"
                )
            except Exception as exc:
                logger.warning("OpenClaw init failed: %s", exc)

        logger.warning(
            "No AI backend available. Set ANTHROPIC_API_KEY or OPENCLAW_BASE_URL."
        )

    @property
    def backend(self) -> str:
        return self._backend

    async def is_available(self) -> bool:
        return self._ai_enabled and self._backend != "none"

    async def analyze(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = MAX_TOKENS_REGIME,
    ) -> str:
        if not await self.is_available():
            return "[AI analysis unavailable — no API key configured or AI_ENABLED=false]"

        system_prompt = system or DEFAULT_SYSTEM

        try:
            if self._backend == "anthropic":
                return await self._call_anthropic(prompt, system_prompt, max_tokens)
            else:
                return await self._call_openclaw(prompt, system_prompt, max_tokens)
        except Exception as exc:
            logger.error("AI API call failed (%s): %s", self._backend, exc)
            return f"[AI analysis temporarily unavailable: {type(exc).__name__}]"

    async def _call_anthropic(
        self, prompt: str, system: str, max_tokens: int
    ) -> str:
        """Call Anthropic Claude API directly."""
        resp = await self._anthropic_client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        # Extract text from content blocks
        text_parts = []
        for block in resp.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
        result = "\n".join(text_parts)
        return result or "[AI returned empty response]"

    async def _call_openclaw(
        self, prompt: str, system: str, max_tokens: int
    ) -> str:
        """Call OpenClaw proxy via OpenAI-compatible API."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        resp = await self._openai_client.chat.completions.create(
            model=f"openclaw:{self._agent_id}",
            messages=messages,
            max_tokens=max_tokens,
        )
        content = resp.choices[0].message.content
        return content or "[AI returned empty response]"

    async def analyze_regime(self, prompt: str) -> str:
        return await self.analyze(prompt, max_tokens=MAX_TOKENS_REGIME)

    async def analyze_weekly(self, prompt: str) -> str:
        return await self.analyze(prompt, max_tokens=MAX_TOKENS_WEEKLY)

    async def analyze_daily(self, prompt: str) -> str:
        return await self.analyze(prompt, max_tokens=MAX_TOKENS_DAILY)
