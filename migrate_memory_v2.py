# -*- coding: utf-8 -*-
"""
DAON 기억 시스템 Phase 1-D: 기존 데이터 일괄 정리 마이그레이션
================================================================
실행: python migrate_memory_v2.py [--dry-run]

작업 내용:
1. 프로필 key 정규화: 분산된 key를 CANONICAL_PROFILE_KEYS로 병합
2. facts 의미 병합: LLM으로 유사 facts 클러스터링 → 대표 fact로 통합
3. summaries 중복 제거: session_id당 최신 1건만 유지

--dry-run: 변경 사항만 출력하고 실제 DB는 수정하지 않음.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# ── 경로 설정 ──
# 설치빌드: LOCALAPPDATA/Daon, 개발: api/data
try:
    import os
    _local = os.environ.get('LOCALAPPDATA')
    if _local and (Path(_local) / 'Daon' / 'memory.db').exists():
        DB_PATH = Path(_local) / 'Daon' / 'memory.db'
    else:
        DB_PATH = Path(__file__).parent / 'api' / 'data' / 'memory.db'
except Exception:
    DB_PATH = Path(__file__).parent / 'api' / 'data' / 'memory.db'

# ── 정규화 매핑 (memory_store.py와 동일) ──
PROFILE_KEY_ALIASES = {
    '이름': 'name', 'name': 'name', '이름(호칭)': 'name',
    '직업': 'occupation', 'occupation': 'occupation', '하는 일': 'occupation',
    '선호언어': 'preferred_language', '선호 언어': 'preferred_language',
    '사용언어': 'preferred_language', '언어': 'preferred_language',
    'preferred_language': 'preferred_language', 'language': 'preferred_language',
    '작업 디렉토리': 'workspace', 'workspace': 'workspace', '작업디렉토리': 'workspace',
    '사용 도구': 'tools', 'tools': 'tools', '도구': 'tools',
    '관련 에이전트': 'agents', 'agents': 'agents', '에이전트': 'agents',
    '목표': 'goals', 'goals': 'goals', '목표사항': 'goals',
    '대화 스타일': 'style', 'style': 'style', '스타일': 'style',
    '메모': 'notes', 'notes': 'notes', '기타': 'notes',
}

CANONICAL_PROFILE_KEYS = {
    'name': '이름',
    'occupation': '직업',
    'preferred_language': '선호 언어',
    'workspace': '작업 디렉토리',
    'tools': '사용 도구',
    'agents': '관련 에이전트',
    'goals': '목표',
    'style': '대화 스타일',
    'notes': '메모',
}


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def migrate_profile(conn: sqlite3.Connection, dry_run: bool) -> int:
    """프로필 key 정규화: 분산 key → 정규 key 병합."""
    rows = conn.execute('SELECT key, value, updated_at FROM profile ORDER BY updated_at DESC').fetchall()
    merged: dict[str, tuple[str, str]] = {}  # canonical_key → (value, updated_at)
    to_delete: list[str] = []
    changes = 0

    for r in rows:
        raw_key = r['key']
        # 내부 메타키 유지
        if raw_key.startswith('_'):
            continue
        canonical = PROFILE_KEY_ALIASES.get(raw_key.strip().lower())
        if canonical is None:
            # 매핑 불가 → notes로 흡수
            canonical = 'notes'
        if canonical not in merged:
            merged[canonical] = (r['value'], r['updated_at'])
        # 이미 정규 key가 있으면 최신 값 유지 (updated_at DESC이므로 첫 번째가 최신)
        if raw_key != canonical:
            to_delete.append(raw_key)
            changes += 1

    print(f"\n[Profile] {len(rows)}개 key → {len(merged)}개 정규 key (+ 내부 메타키)")
    if dry_run:
        for k in to_delete:
            print(f"  삭제 예정: '{k}'")
        for k, (v, _) in merged.items():
            print(f"  유지: '{k}' = '{v[:50]}...'")
        return changes

    # 실제 적용
    for k in to_delete:
        conn.execute('DELETE FROM profile WHERE key=?', (k,))
    for k, (v, ts) in merged.items():
        conn.execute(
            "INSERT INTO profile (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (k, v, ts),
        )
    conn.commit()
    print(f"  적용 완료: {changes}개 key 삭제/병합")
    return changes


def migrate_summaries(conn: sqlite3.Connection, dry_run: bool) -> int:
    """summaries 중복 제거: session_id당 최신 1건만 유지."""
    dupes = conn.execute(
        "SELECT session_id, COUNT(*) AS cnt FROM summaries "
        "GROUP BY session_id HAVING cnt > 1"
    ).fetchall()
    total_removed = 0

    if not dupes:
        print("\n[Summaries] 중복 없음 — 스킵")
        return 0

    print(f"\n[Summaries] 중복 세션 {len(dupes)}건 발견")
    for d in dupes:
        sid = d['session_id']
        cnt = d['cnt']
        # 최신 1건 제외 삭제
        if not dry_run:
            conn.execute(
                "DELETE FROM summaries WHERE session_id=? AND id NOT IN ("
                "  SELECT MAX(id) FROM summaries WHERE session_id=?"
                ")",
                (sid, sid),
            )
        total_removed += cnt - 1
        print(f"  세션 '{sid}': {cnt}건 → 1건 ({cnt - 1}건 삭제)")

    if not dry_run:
        conn.commit()
    print(f"  총 {total_removed}건 삭제 {'(dry-run)' if dry_run else '적용 완료'}")
    return total_removed


def migrate_facts(conn: sqlite3.Connection, dry_run: bool) -> int:
    """facts 의미 병합: LLM으로 클러스터링 → 대표 fact로 통합.

    LLM 호출이 불가능하면(모듈 없음 등) 단순 완전 중복만 제거.
    """
    rows = conn.execute(
        'SELECT id, content, category, created_at FROM facts ORDER BY id ASC'
    ).fetchall()
    facts = [dict(r) for r in rows]
    total = len(facts)
    print(f"\n[Facts] 총 {total}건")

    if total < 5:
        print("  5건 미만 — 병합 스킵")
        return 0

    # 1) 완전 중복 제거 (내용 동일)
    seen_contents: dict[str, int] = {}
    exact_dupes: list[int] = []
    for f in facts:
        c = f['content'].strip().lower()
        if c in seen_contents:
            exact_dupes.append(f['id'])
        else:
            seen_contents[c] = f['id']

    if exact_dupes:
        print(f"  완전 중복 {len(exact_dupes)}건 발견")
        if not dry_run:
            conn.execute(
                f"DELETE FROM facts WHERE id IN ({','.join('?' * len(exact_dupes))})",
                exact_dupes,
            )
            conn.commit()

    # 2) LLM 의미 병합 (선택적)
    try:
        sys.path.insert(0, str(Path(__file__).parent / 'api'))
        from api.dynamic.direct_calls import _call_direct
    except Exception:
        print("  LLM 모듈 없음 — 완전 중복 제거만 수행")
        return len(exact_dupes)

    # 최신 facts 재로드
    rows2 = conn.execute(
        'SELECT id, content FROM facts ORDER BY id DESC LIMIT 200'
    ).fetchall()
    active = [dict(r) for r in rows2]
    if len(active) < 5:
        return len(exact_dupes)

    facts_text = '\n'.join(f"[id={f['id']}] {f['content']}" for f in active)
    prompt = (
        '아래 기억 항목들을 의미적으로 그룹화하라. 같은 주제의 항목끼리 묶어라.\n'
        '반드시 JSON 배열의 배열로 응답하라. 예: [[1,5,12],[3,7],[2]]\n'
        '그룹에 속하지 않는 항목은 단독 배열로. 모든 id를 포함하라.\n\n'
        f'항목:\n{facts_text}'
    )

    try:
        raw = _call_direct(prompt)
        # 코드 펜스 제거
        s = (raw or '').strip()
        if s.startswith('```'):
            s = s.strip('`')
            if s.lower().startswith('json'):
                s = s[4:]
            s = s.strip()
        clusters = json.loads(s)
    except Exception as e:
        print(f"  LLM 클러스터링 실패: {e}")
        return len(exact_dupes)

    merged_count = 0
    for cluster in clusters:
        if not isinstance(cluster, list) or len(cluster) < 2:
            continue
        ids = [int(i) for i in cluster if isinstance(i, (int, float))]
        if len(ids) < 2:
            continue
        cluster_facts = [f for f in active if f['id'] in ids]
        if len(cluster_facts) < 2:
            continue

        contents = '\n'.join(f"[id={f['id']}] {f['content']}" for f in cluster_facts)
        merge_prompt = (
            '아래 같은 주제의 기억 항목들을 하나의 문장으로 통합하라.\n'
            '핵심 정보만 남기고 중복은 제거하라. 통합 문장만 출력하라.\n\n'
            f'{contents}'
        )
        try:
            merged_text = (_call_direct(merge_prompt) or '').strip()
        except Exception:
            continue
        if not merged_text:
            continue

        rep = cluster_facts[0]  # 첫 번째를 대표로
        others = cluster_facts[1:]
        print(f"  병합: {[f['id'] for f in cluster_facts]} → '{merged_text[:60]}...'")

        if not dry_run:
            conn.execute('UPDATE facts SET content=? WHERE id=?', (merged_text, rep['id']))
            for o in others:
                conn.execute('DELETE FROM facts WHERE id=?', (o['id'],))
            conn.commit()
        merged_count += len(others)

    final_count = conn.execute('SELECT COUNT(*) AS c FROM facts').fetchone()['c']
    print(f"  결과: {total}건 → {final_count}건 ({total - final_count}건 감소)")
    return total - final_count


def main():
    parser = argparse.ArgumentParser(description='DAON 기억 시스템 Phase 1-D 마이그레이션')
    parser.add_argument('--dry-run', action='store_true', help='변경 사항만 출력, DB 미수정')
    parser.add_argument('--db', type=str, default=None, help='DB 경로 (기본: 자동 감지)')
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH
    if not db_path.exists():
        print(f"ERROR: DB 파일 없음: {db_path}")
        sys.exit(1)

    print(f"DB: {db_path}")
    print(f"모드: {'DRY-RUN (변경 없음)' if args.dry_run else '실제 적용'}")
    print("=" * 60)

    conn = connect(db_path)
    try:
        # 스키마 확인: facts에 confidence 컬럼이 없으면 마이그레이션 전 상태
        cols = [r[1] for r in conn.execute('PRAGMA table_info(facts)').fetchall()]
        if 'confidence' not in cols:
            print("\n[WARN] facts 테이블에 confidence 컬럼이 없습니다.")
            print("  memory_store.py를 먼저 업데이트한 후 서버를 1회 시작하세요.")
            print("  (_ensure_schema()가 새 컬럼을 자동 추가합니다.)")
            print("  이 스크립트는 컬럼 추가 후 실행하는 것을 권장합니다.")
            print("  계속 진행합니다 (기존 데이터 정리만 수행)...")

        p = migrate_profile(conn, args.dry_run)
        s = migrate_summaries(conn, args.dry_run)
        f = migrate_facts(conn, args.dry_run)

        print("\n" + "=" * 60)
        print(f"완료: 프로필 {p}건, 요약 {s}건, facts {f}건 정리")
        if args.dry_run:
            print("(dry-run 모드 — 실제 변경 없음)")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
