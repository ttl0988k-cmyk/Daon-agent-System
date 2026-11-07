# -*- coding: utf-8 -*-
"""
api.memory_retrieval - DAON SmartRetrieval 5단계 랭킹 파이프라인 (Ruflo 포팅)
========================================================================
LLM/외부 API 호출 0원으로 동작하는 지능형 검색:
  Step 1: 쿼리 확장 (동의어 사전 기반 템플릿 변형)
  Step 2: Fan-out + RRF 융합 (Dense 임베딩 코사인 + Sparse 키워드 매칭, k=60)
  Step 3: 최신성 부스트 (지수 감쇠, half-life 30일, pinned는 최대 유지)
  Step 4: MMR 다양성 필터 (token-Jaccard, lambda=0.7)
  Step 5: 세션 라운드로빈 (특정 세션 편중 방지)

최종: 5~8개 고품질 facts + 최근 PID episodic 요약 1~2개 반환
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

try:
    from api.memory_embed import cosine, embed_text, vector_from_blob
except Exception:
    try:
        from memory_embed import cosine, embed_text, vector_from_blob
    except Exception:
        from api.api.memory_embed import cosine, embed_text, vector_from_blob

_log = logging.getLogger("api.memory_retrieval")

_SYNONYMS_CACHE = None
_SYNONYMS_MTIME = 0.0


def _load_synonyms() -> dict[str, list[str]]:
    """data/memory_synonyms.json을 로드한다. 파일 갱신 시 자동 리로드."""
    global _SYNONYMS_CACHE, _SYNONYMS_MTIME
    syn_file = Path(__file__).resolve().parent.parent.parent / "data" / "memory_synonyms.json"
    if not syn_file.exists():
        return {}

    try:
        mtime = syn_file.stat().st_mtime
        if _SYNONYMS_CACHE is not None and mtime == _SYNONYMS_MTIME:
            return _SYNONYMS_CACHE

        with open(syn_file, "r", encoding="utf-8") as f:
            _SYNONYMS_CACHE = json.load(f)
            _SYNONYMS_MTIME = mtime
            return _SYNONYMS_CACHE
    except Exception as e:
        _log.warning("[MemoryRetrieval] 동의어 사전 로드 실패: %s", e)
        return _SYNONYMS_CACHE or {}


# ---------------------------------------------------------------------------
# Step 1: 쿼리 확장 (Query Expansion, 템플릿 기반, LLM 0회)
# ---------------------------------------------------------------------------
def expand_query(query: str) -> list[str]:
    """원본 쿼리에 대해 동의어 사전을 참조하여 2~3개의 검색 변형을 생성한다."""
    if not query or not query.strip():
        return [""]

    q = query.strip()
    syns = _load_synonyms()
    expansions = [q]

    # 동의어 치환 변형 생성
    matched_replacements: list[tuple[str, str]] = []
    for k, v_list in syns.items():
        if k.lower() in q.lower() and v_list:
            for v in v_list[:2]:
                pattern = re.compile(re.escape(k), re.IGNORECASE)
                alt_q = pattern.sub(v, q)
                if alt_q != q and alt_q not in expansions:
                    matched_replacements.append((k, alt_q))

    for _, alt_q in matched_replacements[:2]:
        if alt_q not in expansions:
            expansions.append(alt_q)

    # 변형이 부족하면 핵심 키워드 조합 추가
    if len(expansions) < 2:
        words = [w for w in re.split(r"\s+", q) if len(w) > 1]
        if len(words) > 2:
            expansions.append(" ".join(words[:4]))

    return expansions[:3]


# ---------------------------------------------------------------------------
# Step 2: Dense + Sparse Scoring & RRF 융합
# ---------------------------------------------------------------------------
def _sparse_score(query: str, content: str) -> float:
    """단어 일치 및 부분 일치 기반의 가벼운 sparse 점수 (0.0 ~ 1.0)."""
    if not query or not content:
        return 0.0

    q_words = set(re.findall(r"[a-zA-Z0-9가-힣]+", query.lower()))
    c_words = set(re.findall(r"[a-zA-Z0-9가-힣]+", content.lower()))

    if not q_words or not c_words:
        return 0.0

    # 자카드 일치 + 부분 단어 일치 가중
    exact_matches = len(q_words & c_words)
    partial_matches = 0
    for qw in q_words:
        if any(qw in cw or cw in qw for cw in c_words if qw != cw):
            partial_matches += 1

    return (exact_matches * 1.0 + partial_matches * 0.4) / (len(q_words) + 1e-5)


def _compute_rrf(rank_lists: list[list[int]], k: int = 60) -> dict[int, float]:
    """여러 랭킹 목록에 대해 Reciprocal Rank Fusion (k=60) 점수를 계산."""
    rrf_scores: dict[int, float] = {}
    for r_list in rank_lists:
        for rank, item_id in enumerate(r_list, start=1):
            rrf_scores[item_id] = rrf_scores.get(item_id, 0.0) + (1.0 / (k + rank))
    return rrf_scores


# ---------------------------------------------------------------------------
# Step 3: 최신성 부스트 (Recency Boost)
# ---------------------------------------------------------------------------
def _recency_multiplier(created_at: Any, pinned: int = 0) -> float:
    """지수 감쇠 (half-life 30일, 최대 +20%). pinned=1은 최대 +20% 영구 유지."""
    if pinned:
        return 1.20

    if not created_at:
        return 1.0

    try:
        if isinstance(created_at, (int, float)):
            ts = float(created_at)
        else:
            from datetime import datetime
            dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            ts = dt.timestamp()
    except Exception:
        ts = time.time()

    days = max(0.0, (time.time() - ts) / 86400.0)
    decay = math.exp(-math.log(2.0) * (days / 30.0))
    return 1.0 + 0.20 * decay


# ---------------------------------------------------------------------------
# Step 4: MMR (Maximal Marginal Relevance) 다양성 필터
# ---------------------------------------------------------------------------
def _token_jaccard(s1: str, s2: str) -> float:
    """두 문자열의 단어 토큰 자카드 유사도."""
    t1 = set(re.findall(r"[a-zA-Z0-9가-힣]+", s1.lower()))
    t2 = set(re.findall(r"[a-zA-Z0-9가-힣]+", s2.lower()))
    if not t1 or not t2:
        return 0.0
    return len(t1 & t2) / len(t1 | t2)


def _mmr_select(candidates: list[dict], target_k: int = 8, lambda_param: float = 0.7) -> list[dict]:
    """token-Jaccard 기반 MMR로 고득점 후보 중 다양성을 확보하여 선택."""
    if len(candidates) <= target_k:
        return candidates

    selected: list[dict] = []
    remaining = candidates.copy()

    # 1번째는 최고점수 후보 선택
    first = remaining.pop(0)
    selected.append(first)

    while len(selected) < target_k and remaining:
        best_score = -float("inf")
        best_idx = 0

        for i, cand in enumerate(remaining):
            rel = cand.get("_score", 0.0)
            max_sim = max(_token_jaccard(cand["content"], sel["content"]) for sel in selected)
            mmr_val = lambda_param * rel - (1.0 - lambda_param) * max_sim
            if mmr_val > best_score:
                best_score = mmr_val
                best_idx = i

        selected.append(remaining.pop(best_idx))

    return selected


# ---------------------------------------------------------------------------
# Step 5: 세션 라운드로빈 (Session Round-Robin)
# ---------------------------------------------------------------------------
def _session_round_robin(items: list[dict], target_k: int) -> list[dict]:
    """특정 source_pid 또는 session_id에 편중되지 않도록 균등 분배."""
    from collections import defaultdict
    groups: dict[str, list[dict]] = defaultdict(list)

    for it in items:
        key = it.get("source_pid") or it.get("session_id") or "global"
        groups[key].append(it)

    result: list[dict] = []
    keys = list(groups.keys())

    idx = 0
    while len(result) < target_k:
        added = False
        for k in keys:
            if idx < len(groups[k]):
                result.append(groups[k][idx])
                added = True
                if len(result) >= target_k:
                    break
        if not added:
            break
        idx += 1

    return result


# ---------------------------------------------------------------------------
# 메인 SmartRetrieval 통합 검색 인터페이스
# ---------------------------------------------------------------------------
def smart_search(
    query: str,
    all_facts: Optional[list[dict]] = None,
    limit: int = 8,
    min_score: float = 0.001,
) -> list[dict]:
    """SmartRetrieval 5단계 알고리즘으로 facts 후보 10개 ➔ 최종 5~8개 정선.
    
    API/LLM 호출 0회. 밀리초 단위 실행.
    all_facts가 주어지지 않으면 DB에서 active facts를 자동 로드한다.
    """
    if not query:
        return []

    if all_facts is None:
        try:
            from api.memory_store import list_facts
            all_facts = list_facts(limit=500, include_superseded=False)
        except Exception:
            all_facts = []

    if not all_facts:
        return []

    t0 = time.perf_counter()

    # Step 1: 쿼리 확장
    queries = expand_query(query)

    # 쿼리 임베딩 벡터 사전 계산
    q_vecs = [vector_from_blob(embed_text(q, is_query=True)) for q in queries]

    rank_lists: list[list[int]] = []

    # Step 2: Fan-out + RRF
    for qi, q_text in enumerate(queries):
        q_vec = q_vecs[qi]

        dense_scored: list[tuple[int, float]] = []
        sparse_scored: list[tuple[int, float]] = []

        for idx, fact in enumerate(all_facts):
            # 무효화(superseded / valid_until 경과) 항목 제외
            if fact.get("valid_until"):
                continue

            # Dense: 임베딩 코사인
            f_blob = fact.get("embedding")
            c_score = 0.0
            if q_vec is not None and f_blob:
                c_score = cosine(q_vec, f_blob)
            dense_scored.append((idx, c_score))

            # Sparse: 키워드 점수
            s_score = _sparse_score(q_text, fact.get("content", ""))
            sparse_scored.append((idx, s_score))

        # Top 10 dense & sparse 추출
        dense_scored.sort(key=lambda x: x[1], reverse=True)
        sparse_scored.sort(key=lambda x: x[1], reverse=True)

        rank_lists.append([x[0] for x in dense_scored[:10]])
        rank_lists.append([x[0] for x in sparse_scored[:10]])

    # RRF 융합 점수
    rrf_map = _compute_rrf(rank_lists, k=60)

    # Step 3: 최신성 부스트 & 점수 결합
    scored_candidates: list[dict] = []
    for idx, rrf in rrf_map.items():
        fact = all_facts[idx]
        mult = _recency_multiplier(fact.get("created_at"), pinned=fact.get("pinned", 0))
        final_score = rrf * mult * float(fact.get("confidence", 1.0))

        if final_score >= min_score:
            cand = dict(fact)
            cand["_score"] = final_score
            scored_candidates.append(cand)

    scored_candidates.sort(key=lambda x: x["_score"], reverse=True)
    top_candidates = scored_candidates[:12]  # 후보 10~12개 확보

    if not top_candidates:
        return []

    # Step 4: MMR 다양성 필터링 (후보에서 상위 목표 개수 정선)
    mmr_filtered = _mmr_select(top_candidates, target_k=limit, lambda_param=0.7)

    # Step 5: 세션 라운드로빈
    final_facts = _session_round_robin(mmr_filtered, target_k=limit)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    _log.info("[SmartRetrieval] 검색 완료: query='%s' 후보=%d -> 최종=%d (%.2f ms)",
              query[:30], len(scored_candidates), len(final_facts), elapsed_ms)

    return final_facts[:limit]


def hybrid_search(query: str, all_facts: Optional[list[dict]] = None, limit: int = 10) -> list[dict]:
    """SmartRetrieval 기반 하이브리드 검색 래퍼 (Dense + Sparse + RRF + MMR)."""
    return smart_search(query=query, all_facts=all_facts, limit=limit)
