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
    from api.dynamic.auth import _get_deepseek_api_key, _get_minimax_api_key

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

    # --- Fallback: Static model enforcement (Phase 1-2) ---
    if preferred_model:
        try:
            from api.managers import model_manager
            _, provider, _ = model_manager.resolve_model_provider(preferred_model)
        except Exception as e:
            _log.warning("Failed to resolve model provider: %s", e)
            provider = None

        pm_lower = preferred_model.lower()
        if provider == 'openrouter' or 'openrouter' in pm_lower:
            # Let OpenRouter through if explicitly chosen
            pass
        elif provider in ('minimax', 'deepseek', 'nvidia'):
            pass
        elif ("minimax" in pm_lower or "deepseek" in pm_lower or "nvidia" in pm_lower or "nemotron" in pm_lower) and "openrouter" not in pm_lower:
            pass
        else:
            # Only fallback if completely unknown
            preferred_model = "MiniMax-M3"
    else:
        preferred_model = "MiniMax-M3"

    # Need to load resolve_model_provider dynamically
    try:
        from api.managers import model_manager
        resolve_model_provider = model_manager.resolve_model_provider
    except ImportError:
        def resolve_model_provider(m): return m, 'custom', 'http://127.0.0.1:11434/v1'

    chain_configs: list[dict] = []
    seen_models: set[str] = set()

    def build_config(m_id: str) -> dict:
        m, p, b = resolve_model_provider(m_id)
        if not p:
            p = 'custom'
        key: str | None = None
        if p == 'openrouter':
            key = os.getenv('OPENROUTER_API_KEY')
        elif p == 'deepseek':
            key = os.getenv('DEEPSEEK_API_KEY') or _get_deepseek_api_key()
        elif p == 'minimax' or p == 'minimax-cn':
            key = os.getenv('MINIMAX_API_KEY') or os.getenv('MINIMAX_CN_API_KEY') or _get_minimax_api_key()
        elif p == 'google' or p == 'gemini':
            key = os.getenv('GOOGLE_API_KEY')
        elif p == 'openai':
            key = os.getenv('OPENAI_API_KEY')
        elif p == 'anthropic':
            key = os.getenv('ANTHROPIC_API_KEY')
        elif p == 'ollama-cloud' or p == 'ollama':
            key = os.getenv('OLLAMA_API_KEY')
        elif p == 'nvidia':
            key = os.getenv('NVIDIA_API_KEY')

        if p == 'custom' and not key:
            key = 'ollama'
        return {"model": m, "provider": p, "base_url": b, "api_key": key}

    # 1. Preferred model (if any)
    if preferred_model and preferred_model not in seen_models:
        cfg = build_config(preferred_model)
        if cfg['api_key'] or cfg['provider'] == 'custom':
            chain_configs.append(cfg)
            seen_models.add(preferred_model)

    # 2. DeepSeek fallback chain
    ds_key = os.getenv('DEEPSEEK_API_KEY') or _get_deepseek_api_key()
    if ds_key:
        for m in ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"]:
            if m not in seen_models:
                chain_configs.append({"model": m, "provider": "deepseek", "base_url": "https://api.deepseek.com/v1", "api_key": ds_key})
                seen_models.add(m)

    # 3. MiniMax fallback chain
    mm_key = os.getenv('MINIMAX_API_KEY') or os.getenv('MINIMAX_CN_API_KEY') or _get_minimax_api_key()
    if mm_key:
        for m in ["MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.5"]:
            if m not in seen_models:
                chain_configs.append({"model": m, "provider": "minimax", "base_url": "https://api.minimax.io/anthropic", "api_key": mm_key})
                seen_models.add(m)

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
