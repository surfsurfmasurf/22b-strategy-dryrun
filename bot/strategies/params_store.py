"""
StrategyParamsStore — 전략 파라미터 런타임 저장소

JSON 파일 기반. 봇 재시작 없이 대시보드/텔레그램에서 파라미터 수정 가능.
각 전략 클래스는 compute() 시 이 스토어에서 파라미터를 읽어간다.

저장 위치: data/strategy_params.json

구조:
{
  "global": {
    "default_tp_pct": 0.02,
    "default_sl_pct": 0.01,
    "min_score_execute": 8,
    "max_active_positions": 2
  },
  "strategies": {
    "overreaction_reversal": {
      "enabled": true,
      "tp_pct": 0.025,
      "sl_pct": 0.012,
      "rsi_oversold": 28.0,
      "rsi_overbought": 72.0,
      "overreaction_pct": 0.03,
      "regime_filter": ["BTC_BEARISH", "BTC_SIDEWAYS", "HIGH_VOLATILITY", "ALT_ROTATION"]
    },
    ...
  }
}
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 기본 전략 파라미터 (전략별 클래스 상수와 동일한 초기값)
DEFAULT_PARAMS: dict = {
    "global": {
        "default_tp_pct":       0.020,
        "default_sl_pct":       0.010,
        "min_score_execute":    8,
        "max_active_positions": 2,
    },
    "strategies": {
        "overreaction_reversal": {
            "enabled":          True,
            "tp_pct":           0.025,
            "sl_pct":           0.012,
            "rsi_oversold":     28.0,
            "rsi_overbought":   72.0,
            "overreaction_pct": 0.030,
            "lookback_bars":    3,
            "ema_long":         200,
            "regime_filter":    ["BTC_BEARISH", "BTC_SIDEWAYS", "HIGH_VOLATILITY", "ALT_ROTATION"],
            "description":      "과매수/과매도 후 급반전 포착 (Reversal)",
        },
        "volatility_expansion_breakout": {
            "enabled":        True,
            "tp_ratio":       0.80,
            "sl_offset":      0.005,
            "vol_mult":       2.0,
            "squeeze_ratio":  1.15,
            "expand_ratio":   1.20,
            "bb_period":      20,
            "regime_filter":  ["BTC_BULLISH", "BTC_SIDEWAYS", "LOW_VOLATILITY"],
            "description":    "Bollinger Squeeze 후 변동성 확장 돌파 (Breakout)",
        },
        "early_trend_capture": {
            "enabled":      True,
            "tp_pct":       0.020,
            "sl_pct":       0.010,
            "rsi_low":      40.0,
            "rsi_high":     60.0,
            "vol_mult":     1.5,
            "ema_fast":     20,
            "ema_slow":     50,
            "atr_period":   14,
            "atr_mult":     1.30,
            "regime_filter": ["BTC_BULLISH", "ALT_ROTATION"],
            "description":  "EMA 골든/데드크로스 초기 추세 포착 (Trend)",
        },
    },
}


class StrategyParamsStore:
    """
    전략 파라미터 런타임 관리자.
    스레드 안전 읽기/쓰기 + JSON 영속성.
    """

    _instance: Optional["StrategyParamsStore"] = None

    def __init__(self, data_dir: str = "data") -> None:
        self._path = Path(data_dir) / "strategy_params.json"
        self._lock = threading.RLock()
        self._params: dict = {}
        self._load()

    # ---------------------------------------------------------------------- #
    # Singleton
    # ---------------------------------------------------------------------- #

    @classmethod
    def get_instance(cls, data_dir: str = "data") -> "StrategyParamsStore":
        if cls._instance is None:
            cls._instance = cls(data_dir)
        return cls._instance

    # ---------------------------------------------------------------------- #
    # Public API
    # ---------------------------------------------------------------------- #

    def get(self, strategy: str, key: str, default: Any = None) -> Any:
        """전략 파라미터 단건 조회. 없으면 DEFAULT_PARAMS → default 순으로 fallback."""
        with self._lock:
            val = self._params.get("strategies", {}).get(strategy, {}).get(key)
            if val is not None:
                return val
            val = DEFAULT_PARAMS.get("strategies", {}).get(strategy, {}).get(key)
            return val if val is not None else default

    def get_global(self, key: str, default: Any = None) -> Any:
        """글로벌 파라미터 조회."""
        with self._lock:
            val = self._params.get("global", {}).get(key)
            if val is not None:
                return val
            return DEFAULT_PARAMS.get("global", {}).get(key, default)

    def get_strategy(self, strategy: str) -> dict:
        """전략 전체 파라미터 dict 반환 (기본값 포함)."""
        with self._lock:
            defaults = dict(DEFAULT_PARAMS.get("strategies", {}).get(strategy, {}))
            overrides = dict(self._params.get("strategies", {}).get(strategy, {}))
            defaults.update(overrides)
            return defaults

    def get_all(self) -> dict:
        """전체 파라미터 (global + 모든 전략) 반환."""
        with self._lock:
            import copy
            result = copy.deepcopy(DEFAULT_PARAMS)
            # 저장된 오버라이드 적용
            for key, val in self._params.get("global", {}).items():
                result["global"][key] = val
            for strat, overrides in self._params.get("strategies", {}).items():
                if strat not in result["strategies"]:
                    result["strategies"][strat] = {}
                result["strategies"][strat].update(overrides)
            return result

    def set_strategy(self, strategy: str, updates: dict) -> None:
        """전략 파라미터 업데이트 후 저장."""
        with self._lock:
            if "strategies" not in self._params:
                self._params["strategies"] = {}
            if strategy not in self._params["strategies"]:
                self._params["strategies"][strategy] = {}
            self._params["strategies"][strategy].update(updates)
            self._save()
        logger.info("[ParamsStore] Updated '%s': %s", strategy, list(updates.keys()))

    def set_global(self, updates: dict) -> None:
        """글로벌 파라미터 업데이트 후 저장."""
        with self._lock:
            if "global" not in self._params:
                self._params["global"] = {}
            self._params["global"].update(updates)
            self._save()
        logger.info("[ParamsStore] Updated global: %s", list(updates.keys()))

    def is_enabled(self, strategy: str) -> bool:
        return bool(self.get(strategy, "enabled", True))

    def get_regime_filter(self, strategy: str) -> List[str]:
        return self.get(strategy, "regime_filter", [])

    # ---------------------------------------------------------------------- #
    # Persistence
    # ---------------------------------------------------------------------- #

    def _load(self) -> None:
        """JSON 파일 로드. 없으면 기본값으로 초기화."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._params = json.load(f)
                logger.info("[ParamsStore] Loaded from %s", self._path)
                return
            except Exception as exc:
                logger.warning("[ParamsStore] Load failed (%s) — using defaults", exc)
        # 최초 실행: 기본값 저장
        self._params = {}
        self._save()

    def _save(self) -> None:
        """현재 파라미터를 JSON 파일에 저장 (오버라이드 값만)."""
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._params, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error("[ParamsStore] Save failed: %s", exc)
