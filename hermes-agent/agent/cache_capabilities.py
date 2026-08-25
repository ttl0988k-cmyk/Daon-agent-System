# -*- coding: utf-8 -*-
"""Central cache-capability resolution for LLM providers.

[캐시 캐퍼빌리티 시스템 Phase 1 — 2026-08-25]

모든 (provider, base_url, api_mode, model) 조합을 와이어 수준 캐시 전략
하나로 분류한다. 호출자는 더 이상 프로바이더 분기를 하드코딩하지 않는다.

전략 3가지:
  explicit — 클라이언트가 캐시 마커를 보내야 한다 (Anthropic cache_control,
             Qwen DashScope 캐시 헤더 등). 마커 주입은 상위 계층(run_agent)이
             담당한다.
  implicit — 프로바이더가 서버 측에서 자동 캐싱한다 (DeepSeek 컨텍스트 캐시,
             Moonshot/Kimi, MiniMax, Zhipu GLM, OpenAI 프리픽스 캐시, Gemini
             암시적 캐시, Codex prompt_cache_key). 우리는 특별한 것을 보내지
             않고 프롬프트 접두사를 안정화한 뒤 usage 의 cache_read_* 를
             관측해 적중률만 보고한다.
  none     — 캐시 미확인/미지원. 기본 거부(default-deny): 마커를 절대 보내지
             않는다. 모르는 조합은 전부 none 으로 수렴한다 (리스크 5 철학과
             동일: 불명 캐퍼빌리티 -> 위험 동작 거부).

``unknown`` 은 전략이 아니라 해석 이전 상태일 뿐이다. Phase 2 에서 런타임
usage 관측으로 implicit/none 을 자기 해소(self-resolve)할 예정이다.

순수 함수 + 정적 테이블. AIAgent 의존 없음. 호출자가 ``overrides`` 를 넘기면
(추후 config.yaml ``cache_capabilities:`` 섹션에 연결 예정) 코드 수정 없이
프로바이더/모델별 전략을 강제할 수 있다.
"""

import fnmatch
from dataclasses import dataclass
from typing import Any, Dict, Optional

__all__ = [
    "CACHE_STRATEGY_EXPLICIT",
    "CACHE_STRATEGY_IMPLICIT",
    "CACHE_STRATEGY_NONE",
    "CacheStrategyResult",
    "resolve_cache_strategy",
]

CACHE_STRATEGY_EXPLICIT = "explicit"
CACHE_STRATEGY_IMPLICIT = "implicit"
CACHE_STRATEGY_NONE = "none"


@dataclass(frozen=True)
class CacheStrategyResult:
    """단일 (provider, base_url, api_mode, model) 조합의 캐시 전략 판정."""

    strategy: str
    native_layout: bool = False
    reason: str = ""

    @property
    def should_inject_markers(self) -> bool:
        """True 면 상위 계층이 cache_control 마커를 주입해야 한다."""
        return self.strategy == CACHE_STRATEGY_EXPLICIT


# 서버 측 자동 캐시(암시적)가 확인된 프로바이더 ID 목록.
# 소문자 정규화한 provider 문자열과 비교한다.
_IMPLICIT_PROVIDER_IDS = frozenset({
    "openai", "openai-codex",
    "deepseek",
    "minimax", "minimax-cn",
    "zai", "glm", "zhipu", "z-ai", "z.ai",
    "kimi-coding", "kimi-coding-cn", "kimi", "moonshot", "moonshot-cn", "kimi-cn",
    "xai", "x-ai", "x.ai",
    "google", "google-gemini", "google-ai-studio", "gemini",
    "qwen", "qwen-oauth", "qwen-portal", "dashscope", "aliyun", "alibaba",
})

# base_url 부분 문자열로도 암시적 캐시를 인정한다 (커스텀 프로바이더가
# provider 이름 없이 직접 URL 만 설정하는 경우 대비).
_IMPLICIT_BASE_FRAGMENTS = (
    "api.openai.com",
    "api.deepseek.com",
    "api.minimax.io",
    "api.minimax.chat",
    "api.minimaxi.com",
    "bigmodel.cn",
    "api.moonshot",
    "api.x.ai",
    "generativelanguage.googleapis.com",
    "dashscope.aliyuncs.com",
)


def _is_claude_model(model: str) -> bool:
    return "claude" in (model or "").lower()


def _match_override(table: Any, key: str) -> Optional[Dict[str, Any]]:
    """overrides 의 models/providers 테이블에서 key 에 대응하는 스펙을 찾는다.

    정확 일치(대소문자 무시)를 우선하고, 없으면 fnmatch 와일드카드("claude-*"
    등)를 허용한다. 못 찾으면 None.
    """
    if not isinstance(table, dict) or not key:
        return None
    key_lower = key.lower()
    # 1) 정확 일치
    for pattern, spec in table.items():
        if (
            isinstance(pattern, str)
            and pattern.lower() == key_lower
            and isinstance(spec, dict)
        ):
            return spec
    # 2) 와일드카드
    for pattern, spec in table.items():
        if (
            isinstance(pattern, str)
            and any(c in pattern for c in "*?[")
            and fnmatch.fnmatch(key_lower, pattern.lower())
            and isinstance(spec, dict)
        ):
            return spec
    return None


def _spec_to_result(spec: Dict[str, Any], reason: str) -> CacheStrategyResult:
    """오버라이드 스펙 dict 를 CacheStrategyResult 로 변환한다.

    허용 키: strategy|prompt_cache (explicit/implicit/none), native_layout.
    알 수 없는 전략 값은 안전하게 none 으로 강등한다 (기본 거부).
    """
    strategy = str(
        spec.get("strategy") or spec.get("prompt_cache") or CACHE_STRATEGY_NONE
    ).lower()
    if strategy not in (CACHE_STRATEGY_EXPLICIT, CACHE_STRATEGY_IMPLICIT, CACHE_STRATEGY_NONE):
        strategy = CACHE_STRATEGY_NONE
    native = bool(spec.get("native_layout", False))
    return CacheStrategyResult(strategy=strategy, native_layout=native, reason=reason)


def resolve_cache_strategy(
    *,
    provider: str = "",
    base_url: str = "",
    api_mode: str = "",
    model: str = "",
    overrides: Optional[Dict[str, Any]] = None,
) -> CacheStrategyResult:
    """와이어 형식 기준으로 캐시 전략을 결정한다.

    매칭 키는 provider 이름이 아니라 실제 전송 형식(api_mode x base_url x
    모델 패밀리)이다. "Anthropic 호환 게이트웨이면 브랜드 불문 네이티브
    레이아웃"이라는 기존 통찰을 그대로 보존한다.

    Resolution 순서:
      0. overrides (models -> providers) — 향후 config.yaml 연결 지점
      1. explicit 와이어 규칙 (기존 if-체인의 정확한 복제 — 동작 무변경)
      2. implicit 테이블 (provider ID 또는 base_url 부분 문자열)
      3. 기본 거부(none)
    """
    prov_lower = (provider or "").strip().lower()
    url_lower = (base_url or "").lower()

    # 0) 사용자 오버라이드 최우선
    if isinstance(overrides, dict):
        m_spec = _match_override(overrides.get("models"), model or "")
        if m_spec is not None:
            return _spec_to_result(m_spec, "override:model")
        p_spec = _match_override(overrides.get("providers"), prov_lower)
        if p_spec is not None:
            return _spec_to_result(p_spec, "override:provider")

    # 1) explicit 규칙 — 구 _anthropic_prompt_cache_policy 의 정확한 복제.
    is_claude = _is_claude_model(model)
    is_openrouter = "openrouter" in url_lower
    is_anthropic_wire = (api_mode or "") == "anthropic_messages"
    is_native_anthropic = is_anthropic_wire and (
        prov_lower == "anthropic" or "api.anthropic.com" in url_lower
    )

    if is_native_anthropic:
        return CacheStrategyResult(
            CACHE_STRATEGY_EXPLICIT, True, "explicit:native_anthropic")
    if is_openrouter and is_claude:
        return CacheStrategyResult(
            CACHE_STRATEGY_EXPLICIT, False, "explicit:openrouter_claude")
    if is_anthropic_wire and is_claude:
        # 서드파티 Anthropic 호환 게이트웨이 (MiniMax/GLM/LiteLLM 프록시 등).
        return CacheStrategyResult(
            CACHE_STRATEGY_EXPLICIT, True, "explicit:anthropic_gateway")

    # 2) implicit: 서버 자동 캐시 — 마커를 보내지 않고 관측만 한다.
    if prov_lower in _IMPLICIT_PROVIDER_IDS:
        return CacheStrategyResult(
            CACHE_STRATEGY_IMPLICIT, False, "implicit:provider=%s" % prov_lower)
    for frag in _IMPLICIT_BASE_FRAGMENTS:
        if frag in url_lower:
            return CacheStrategyResult(
                CACHE_STRATEGY_IMPLICIT, False, "implicit:url~%s" % frag)

    # 3) 기본 거부: 알 수 없는 조합은 마커를 보내지 않는다.
    return CacheStrategyResult(CACHE_STRATEGY_NONE, False, "none:default_deny")
