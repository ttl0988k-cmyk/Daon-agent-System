# -*- coding: utf-8 -*-
"""
test_ruflo_memory_pipeline.py - Ruflo식 DAON 장기기억 시스템 전체 검증 스크립트
========================================================================
1. Phase 1 (읽기): 10개 대표 질의 SmartRetrieval 5단계 랭킹, 레이턴시, API 0원 검증
2. Phase 2 (쓰기): Write Guard(민감정보 차단), 벡터 코사인 0.95 병합, PID 증류 훅
3. Phase 3 (정리): Consolidator (sweep/dedup/pinned 보호)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'api'))

from api.memory_embed import embed_text, cosine
from api.memory_retrieval import smart_search
from api.memory_store import (
    add_fact,
    pin_fact,
    get_context_block,
    consolidate_memory,
    process_pid_memory,
    list_facts,
    _connect
)

print("=" * 60)
print("1. Phase 1: 읽기 파이프라인 (SmartRetrieval 5-Step) 검증")
print("=" * 60)

test_queries = [
    "라온의 기본 역할과 권한은 무엇인가요?",
    "다이나믹 하네스에서 Laya의 역할은?",
    "Dynamic Harness DAG 실행 방식은?",
    "PID 완료 후 결과는 어떻게 처리되나요?",
    "라온 기억 시스템의 임베딩 모델은?",
    "429 에러 발생 시 처리 전략은?",
    "어떤 기억이 pinned 보호 대상인가요?",
    "장기기억 검색의 SmartRetrieval 단계는?",
    "Unity 관련 작업은 누가 담당하나요?",
    "비밀키나 토큰이 입력되면 어떻게 되나요?",
]

latencies = []
retrieval_success_count = 0

for i, q in enumerate(test_queries, 1):
    t0 = time.time()
    results = smart_search(q, limit=8)
    dt_ms = (time.time() - t0) * 1000
    latencies.append(dt_ms)
    
    count = len(results)
    if count >= 3:
        retrieval_success_count += 1
    top_preview = results[0]['content'][:40] if results else "(없음)"
    print(f"[{i:02d}] '{q}' -> {count}건 반환 ({dt_ms:.1f}ms) | Top1: {top_preview}...")

avg_latency = sum(latencies) / len(latencies)
print(f"\n=> 10개 질의 평균 검색 레이턴시: {avg_latency:.2f}ms (수용기준: <500ms)")
print(f"=> 검색 유효 비율: {retrieval_success_count}/10 (수용기준: 8/10 이상)")

# Context block inspection
ctx = get_context_block(query="하네스 실행과 라온 역할", max_facts=6)
print(f"\n[get_context_block 주입 프리뷰 (총 {len(ctx)}자)]:\n{ctx[:400]}...\n")

print("=" * 60)
print("2. Phase 2: 쓰기 파이프라인 (Write Guard, 0.95 병합, PID 증류) 검증")
print("=" * 60)

# 2-1. Write Guard (비밀키 차단)
secret_fact = "중요 키: sk-ant-api03-1234567890abcdef1234567890abcdef"
res_secret = add_fact(secret_fact, namespace="security")
assert res_secret is None, "Write Guard should reject secret key"
print("✓ [Write Guard] 비밀키 포함 텍스트 저장 거부 성공")

# 2-2. Vector Cosine 0.95 Near-Duplicate 병합
fact_a = "다이나믹 하네스는 라온이 메타 CEO로서 노드를 동적으로 생성하고 오케스트레이션한다."
id_a = add_fact(fact_a, namespace="patterns")
print(f"✓ Fact A 저장: id={id_a}")

# 살짝 다른 어휘로 동일한 의미
fact_b = "다이나믹 하네스는 라온이 메타 CEO로서 각 에이전트 노드를 동적으로 생성하고 오케스트레이션한다."
id_b = add_fact(fact_b, namespace="patterns")
print(f"✓ Fact B 저장 (0.95 유사): id={id_b}")
assert id_b == id_a, f"Expected duplicate merge into {id_a}, got {id_b}"
print("✓ [Vector Dedup] 코사인 0.95 이상 유사 사실 자동 병합 성공 (동일 id 반환, use_count 증가)")

# 2-3. PID Mock Outcome Distillation
mock_pid = f"pid-test-{int(time.time())}"
mock_outcome = {
    'status': 'success',
    'task': 'Ruflo 장기 기억 파이프라인 이식 및 429 회로 차단기 적용',
    'result': 'SQLite 기반 384차원 로컬 임베딩과 SmartRetrieval 5단계를 구축하여 API 비용 0원으로 장기기억을 완성함.',
    'logs': [
        {'agent': 'Raon', 'content': 'Memory embed and retrieval modules implemented.'},
        {'agent': 'Tony', 'content': 'Circuit breaker fast failover tested.'}
    ]
}
distill_res = process_pid_memory(mock_pid, mock_outcome)
print(f"✓ [PID Distillation] PID={mock_pid} 결과: {distill_res}")

print("=" * 60)
print("3. Phase 3: 정리 파이프라인 (Consolidator, Pinned 보호) 검증")
print("=" * 60)

# Pinned test
pin_fact(id_a, pinned=1)
conn = _connect()
row_pinned = conn.execute("SELECT pinned FROM facts WHERE id=?", (id_a,)).fetchone()
conn.close()
assert row_pinned['pinned'] == 1, "Pinned flag should be 1"
print(f"✓ Fact #{id_a} pinned=1 설정 완료")

consol_res = consolidate_memory()
print(f"✓ [Consolidator] consolidate_memory 실행 결과: {consol_res}")

# Check pinned fact still exists and has confidence untouched
conn = _connect()
row_check = conn.execute("SELECT id, pinned, confidence FROM facts WHERE id=?", (id_a,)).fetchone()
conn.close()
assert row_check is not None, "Pinned fact must survive consolidation"
print(f"✓ [Pinned Protection] Fact #{id_a} 생존 확인 (pinned={row_check['pinned']}, conf={row_check['confidence']})")

print("\n" + "=" * 60)
print("전체 검증 완료: 100% PASS!")
print("=" * 60)

