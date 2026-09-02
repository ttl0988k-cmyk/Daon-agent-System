"""
DAG utility functions for model chain resolution, context compression,
and DAG structural construction.

Provides:
- _extract_assistant_content(): extract last assistant message from conversation
- _get_model_chain_for_node(): build model fallback chain for a node
- _compress_context(): trim redundant whitespace from context blobs
- _build_dag_structures(): build in_degree/adj_list/parent_list from edges
- _compute_execution_batches(): topological sort into parallel batches
"""

import os
import re
from collections import deque

from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)


def _extract_assistant_content(messages: list) -> str:
    """Extract last assistant message text from a messages list."""
    for m in reversed(messages):
        if m.get("role") == "assistant":
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
            return content
    return ""


def _get_model_chain_for_node(preferred_model: str, role: str = "",
                               task: str = "", required_strength: str = "code",
                               required_context: int = 32000) -> list[dict]:
    """Return a list of dicts with model, provider, api_key, base_url for cross-provider fallback.

    Phase 3: When role + task context is available, delegates to DynamicModelSelector
    for multi-factor scoring. Falls back to static chain when context is insufficient.
    """
    # API keys are resolved dynamically per provider in build_config() below.

    # --- Try DynamicModelSelector first (Phase 3) ---
    if role and task:
        try:
            from api.dynamic.model_selector import get_model_selector
            selector = get_model_selector()
            chain, context_info = selector.select_for_node(
                role=role, task=task,
                preferred_model=preferred_model or None,
                required_strength=required_strength,
                required_context=required_context,
                top_k=3,
            )
            if chain:
                _log.info(
                    "DynamicModelSelector: role=%s, strength=%s, ctx=%s → %s",
                    role, required_strength,
                    context_info.get('context_keys', []),
                    [c['model'] for c in chain],
                )
                return chain
        except Exception as e:
            _log.info("DynamicModelSelector unavailable, using static chain: %s", e)

    # --- Fallback: SAME-PROVIDER chain only (no cross-provider billing) ---
    #
    # 이전 구현은 custom_providers.json 의 "전체" 프로바이더 모델을 폴백 체인에
    # 순서대로 쏟아부었다. 그 결과 세션 요약 같은 백그라운드 호출이 사용자가
    # 선택하지 않은 유료 프로바이더(예: openrouter/luna)까지 순회하며 크레딧을
    # 소진했다. 이제는 "기준(선호) 모델과 같은 프로바이더" 안에서만 폴백한다.
    # 선호 모델이 없으면 "제일 먼저 키가 확인된 프로바이더 그룹 하나"만 사용한다.
    chain_configs: list[dict] = []
    seen_models: set[str] = set()

    try:
        from api.managers import model_manager
        resolve_model_provider = model_manager.resolve_model_provider
    except ImportError:
        def resolve_model_provider(m): return m, 'custom', None

    def build_config(m_id: str) -> dict:
        m, p, b = resolve_model_provider(m_id)
        if not p:
            p = 'custom'
        key: str | None = None
        try:
            from api.dynamic.auth import _resolve_key_from_pool
            key = _resolve_key_from_pool(p)
        except Exception:
            key = None
        if not key:
            key = os.getenv(f'{p.upper()}_API_KEY')
        return {"model": m, "provider": p, "base_url": b, "api_key": key}

    # 1. Preferred model (if any) — this also pins the anchor provider.
    anchor_provider: str | None = None
    if preferred_model and preferred_model not in seen_models:
        cfg = build_config(preferred_model)
        if cfg['api_key'] or cfg['provider'] == 'custom':
            chain_configs.append(cfg)
            seen_models.add(preferred_model)
        anchor_provider = cfg['provider']

    # 2. Other models, but ONLY from the anchor provider's group.
    try:
        from api.managers import model_manager as _mm
        # kimi-k3 사고 대책(2026-09-03): 프리셋(opencode-go 33모델)이 groups[0]이면
        # 선호 모델 없는 백그라운드 호출(memory_store 추출/정제)의 앵커가
        # 사용자가 선택한 적 없는 유료 프로바이더로 정해진다.
        # 사용자 등록(is_custom) 프로바이더를 프리셋보다 먼저 본다.
        groups = sorted(_mm.get_available_models(),
                        key=lambda g: 0 if g.get('is_custom') else 1)
        for group in groups:
            gprov = group.get('provider_key') or group.get('provider')
            # 기준 프로바이더가 정해졌으면 그 프로바이더 그룹만 본다.
            if anchor_provider and gprov != anchor_provider:
                continue
            for m in group.get('models', []):
                mid = m.get('id') if isinstance(m, dict) else str(m)
                if not mid or mid in seen_models:
                    continue
                # 이미지/비디오 모델은 chat 폴백 체인에서 제외 (타입 표기 있을 때만)
                if isinstance(m, dict) and m.get('type') and m.get('type') != 'chat':
                    continue
                cfg = build_config(mid)
                if cfg['api_key'] or cfg['provider'] == 'custom':
                    chain_configs.append(cfg)
                    seen_models.add(mid)
            if anchor_provider:
                # 기준 프로바이더 그룹을 처리했으면 다른 그룹은 보지 않는다.
                break
            if chain_configs:
                # 선호 모델이 없으면, 모델이 확인된 첫 프로바이더를 기준으로 고정.
                anchor_provider = gprov
                break
    except Exception as e:
        _log.info("Dynamic fallback chain resolution failed: %s", e)

    return chain_configs


def _compress_context(content: str) -> str:
    """Compress context content to reduce token usage by trimming duplicate newlines and excessive whitespace."""
    if not content:
        return ""
    # 3개 이상의 연속된 줄바꿈을 2개로 압축
    content = re.sub(r'\n{3,}', '\n\n', content)
    # 4개 이상의 연속된 공백을 단일 탭 크기로 축소
    content = re.sub(r' {4,}', '    ', content)
    return content.strip()


def _build_dag_structures(agents: list[dict], edges: list) -> tuple[dict, dict, dict]:
    """Build in_degree, adj_list, and parent_list for DAG topological execution."""
    in_degree = {a["name"]: 0 for a in agents}
    adj_list = {a["name"]: [] for a in agents}
    parent_list = {a["name"]: [] for a in agents}
    for edge in edges:
        if len(edge) >= 2:
            src, dest = edge[0].strip().lower(), edge[1].strip().lower()
            if src in adj_list and dest in in_degree:
                adj_list[src].append(dest)
                in_degree[dest] += 1
                parent_list[dest].append(src)
    return in_degree, adj_list, parent_list


def _compute_execution_batches(in_degree: dict, adj_list: dict) -> list[list[str]]:
    """Compute parallel execution batches via Kahn's topological sort algorithm."""
    batches: list[list[str]] = []
    work_degree = dict(in_degree)
    queue: deque[str] = deque(n for n, d in work_degree.items() if d == 0)
    while queue:
        batch = list(queue)
        batches.append(batch)
        queue.clear()
        for parent in batch:
            for child in adj_list[parent]:
                work_degree[child] -= 1
        batched = {n for b in batches for n in b}
        queue.extend(n for n, d in work_degree.items() if d == 0 and n not in batched)
    return batches
