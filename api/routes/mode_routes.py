"""
Mode System route helpers — Roo Code-style mode enforcement.
Supports: code, architect, debug, ask, orchestrator, review, test.

Each mode has:
- Allowed file operations (read, write, diff, delete, create)
- Allowed tool categories (terminal, browser, git, integration, etc.)
- Permission mask applied at route dispatch level
"""

from api.helpers import j, bad
from api.config import STATE_DIR

# ── Mode definitions ──────────────────────────────────────────────────────────

MODES = {
    'code': {
        'label': '💻 Code',
        'description': '모든 파일에 대해 코드 작성, 수정, 리팩토링',
        'file_ops': ['read', 'write', 'diff', 'delete', 'create', 'rename', 'mkdir'],
        'tools': ['terminal', 'browser', 'git', 'integration', 'docs', 'checkpoint'],
        'icon': '💻',
    },
    'architect': {
        'label': '🏗️ Architect',
        'description': '설계, 기획, 전략 수립 — 마크다운 파일만 수정 가능',
        'file_ops': ['read', 'diff', 'create'],
        'tools': ['docs', 'checkpoint'],
        'icon': '🏗️',
        'write_allowed_exts': ['.md', '.txt', '.yaml', '.yml', '.json', '.toml'],
    },
    'debug': {
        'label': '🪲 Debug',
        'description': '문제 진단, 로깅 추가, 스택 트레이스 분석',
        'file_ops': ['read', 'diff'],
        'tools': ['terminal', 'git', 'checkpoint'],
        'icon': '🪲',
    },
    'ask': {
        'label': '❓ Ask',
        'description': '읽기 전용 — 설명과 분석만 수행',
        'file_ops': ['read'],
        'tools': ['docs'],
        'icon': '❓',
    },
    'orchestrator': {
        'label': '🪃 Orchestrator',
        'description': '도메인 간 멀티 에이전트 워크플로 조정',
        'file_ops': ['read', 'write', 'diff', 'delete', 'create', 'rename', 'mkdir'],
        'tools': ['terminal', 'browser', 'git', 'integration', 'docs', 'checkpoint'],
        'icon': '🪃',
    },
    'review': {
        'label': '👁️ Review',
        'description': '코드 변경사항 검토 및 피드백 — 읽기 전용',
        'file_ops': ['read'],
        'tools': ['git', 'docs'],
        'icon': '👁️',
    },
    'test': {
        'label': '🧪 Test',
        'description': '테스트 작성 및 실행 — 테스트 파일만 수정 가능',
        'file_ops': ['read', 'write', 'diff', 'create'],
        'tools': ['terminal', 'git'],
        'icon': '🧪',
        'write_allowed_patterns': ['test_', '_test.', 'spec.', '.test.', '.spec.', '__tests__/'],
    },
}

DEFAULT_MODE = 'code'

# ── Active mode storage (per-session) ─────────────────────────────────────────

# { session_id: mode_slug }
_active_modes = {}


def get_session_mode(session_id: str) -> str:
    """Get the active mode for a session. Defaults to 'code'."""
    return _active_modes.get(session_id, DEFAULT_MODE)


def set_session_mode(session_id: str, mode: str) -> None:
    """Set the active mode for a session."""
    if mode in MODES:
        _active_modes[session_id] = mode


def check_file_op_allowed(mode: str, op: str, file_path: str = '') -> tuple[bool, str]:
    """Check if a file operation is allowed in the given mode.

    Returns (allowed: bool, reason: str).
    """
    if mode not in MODES:
        return False, f"Unknown mode: {mode}"
    m = MODES[mode]
    if op not in m['file_ops']:
        return False, f"Mode '{mode}' does not allow '{op}' operations"

    # Architect mode: only allow writes to config/doc files
    if mode == 'architect' and op in ('write', 'create', 'delete', 'rename', 'mkdir'):
        allowed_exts = m.get('write_allowed_exts', [])
        if allowed_exts:
            from pathlib import Path
            ext = Path(file_path).suffix.lower()
            if ext not in allowed_exts:
                return False, (
                    f"Architect mode only allows writing to: {', '.join(allowed_exts)}. "
                    f"Got file: {file_path}"
                )

    # Test mode: only allow writes to test files
    if mode == 'test' and op in ('write', 'create', 'diff'):
        patterns = m.get('write_allowed_patterns', [])
        if patterns:
            from pathlib import Path
            fname = Path(file_path).name
            full_path = file_path.replace('\\', '/')
            matched = any(p in fname or p in full_path for p in patterns)
            if not matched:
                return False, (
                    f"Test mode only allows writing to test files "
                    f"(patterns: {', '.join(patterns)}). Got: {file_path}"
                )

    return True, ''


# ── GET /api/modes ────────────────────────────────────────────────────────────

def handle_get_modes(handler, parsed) -> bool:
    """GET /api/modes — list all available modes with their capabilities."""
    return j(handler, {
        'modes': {k: {
            'label': v['label'],
            'description': v['description'],
            'file_ops': v['file_ops'],
            'tools': v['tools'],
            'icon': v['icon'],
        } for k, v in MODES.items()},
        'default': DEFAULT_MODE,
    })


# ── GET /api/mode — get current session mode ──────────────────────────────────

def handle_get_mode(handler, parsed) -> bool:
    """GET /api/mode?session_id=... — get current mode for a session."""
    from urllib.parse import parse_qs
    qs = parse_qs(parsed.query)
    sid = qs.get('session_id', [''])[0]
    if not sid:
        return bad(handler, 'session_id is required')
    mode = get_session_mode(sid)
    return j(handler, {
        'session_id': sid,
        'mode': mode,
        'mode_info': MODES.get(mode, MODES[DEFAULT_MODE]),
    })


# ── POST /api/mode — set session mode ─────────────────────────────────────────

def handle_post_mode(handler, body) -> bool:
    """POST /api/mode — switch the mode for a session.

    Body: { session_id, mode }
    Returns: { ok, session_id, mode, mode_info }
    """
    sid = body.get('session_id', '').strip()
    mode = body.get('mode', '').strip()

    if not sid:
        return bad(handler, 'session_id is required')
    if not mode:
        return bad(handler, 'mode is required')
    if mode not in MODES:
        return bad(handler, f"Unknown mode: {mode}. Available: {', '.join(MODES.keys())}")

    set_session_mode(sid, mode)
    return j(handler, {
        'ok': True,
        'session_id': sid,
        'mode': mode,
        'mode_info': MODES[mode],
    })


# ── Mode check helper (for route dispatch interop) ────────────────────────────

def enforce_mode_for_op(session_id: str, op: str, file_path: str = '') -> None:
    """Raise ValueError if the current session mode doesn't allow this file op."""
    mode = get_session_mode(session_id)
    allowed, reason = check_file_op_allowed(mode, op, file_path)
    if not allowed:
        raise ValueError(f"Mode restriction: {reason}")


# ── POST /api/mode/intent — analyze user message to suggest the best mode ─────

# Keyword-based intent detection rules: (mode_slug, priority_score, keywords)
_INTENT_RULES = [
    ('debug', 90, ['버그', '에러', '오류', '디버그', '디버깅', '안 돼', '안돼', '고장', '문제가 생겼',
                   'debug', 'error', 'bug', 'fix', 'broken', 'not working', 'crash', '크래시',
                   'stack trace', 'traceback', '로그', 'log', '디버그해', '고쳐줘', '수정해줘',
                   '왜 안', '뭐가 문제', '어디서 터', '확인해줘']),
    ('architect', 90, ['설계', '기획', '구조', '아키텍처', 'architecture', 'design',
                       '계획', '플랜', 'plan', '전략', 'strategy', '방안', '로드맵',
                       '어떻게 만들', '설명해줘', '분석', 'analyze', '어떤 방식',
                       '추천', 'recommend', '제안', '아이디어', '아이디어']),
    ('ask', 85, ['뭐야', '뭐니', '궁금', '설명', '무슨', '알려줘', '무엇', '어떻게 동작',
                 'what is', 'how does', 'explain', '의미', '뜻', '개념', '정보',
                 '차이', '비교', 'compare', 'difference', '알고 싶', '가르쳐',
                 '정의', 'definition', '용어', '기능이 뭐', '사용법', 'usage']),
    ('test', 85, ['테스트', 'test', '단위 테스트', 'unit test', '통합 테스트', 'integration test',
                  '테스트 코드', 'test code', '커버리지', 'coverage', 'TDD',
                  'mock', 'stub', 'assert', '검증', 'verify', '테스트해줘',
                  '테스트 작성', '품질', 'QA']),
    ('review', 80, ['리뷰', 'review', '검토', '코드 리뷰', 'code review', '점검',
                    '체크', '확인', '검사', 'inspect', 'audit', '평가',
                    'feedback', '피드백', '괜찮은지', '어때', '문제없', '보안',
                    'security', 'vulnerability', '취약점', '개선점']),
    ('orchestrator', 75, ['여러', '모두', '전부', '한번에', '동시에', '병렬', 'parallel',
                          '여러 개', '다중', 'multi', '워크플로', 'workflow', '파이프라인',
                          'pipeline', '자동화', 'automation', '여러 작업', '복합',
                          '전체', '시스템', 'system', '배포', 'deploy', 'release']),
    ('code', 70, ['코드', 'code', '구현', 'implement', '작성', 'write', '만들어', '생성',
                  '개발', 'develop', '수정', 'modify', '변경', 'change', '업데이트',
                  'update', '추가', 'add', '삭제', 'remove', 'delete', '리팩토링', 'refactor',
                  '빌드', 'build', '컴파일', 'compile', '기능', 'feature', '패치', 'patch',
                  '함수', 'function', '클래스', 'class', '컴포넌트', 'component']),
]


def detect_mode_intent(user_message: str) -> list[dict]:
    """Analyze user message and return ranked mode suggestions.

    Returns a list of { mode, label, icon, description, confidence } objects,
    sorted by confidence (highest first). Always includes at least 'code' as fallback.
    """
    text = user_message.lower()
    scores = {slug: 0 for slug in MODES}

    for slug, base_priority, keywords in _INTENT_RULES:
        for kw in keywords:
            if kw.lower() in text:
                scores[slug] = max(scores[slug], base_priority)
                break  # one keyword match is enough per rule

    # If nothing matched, return empty — let the default mode handle it
    if max(scores.values()) == 0:
        return []

    # Normalize to 0-100 confidence and sort
    max_score = max(scores.values())
    results = []
    for slug, raw_score in sorted(scores.items(), key=lambda x: -x[1]):
        if raw_score > 0:
            confidence = min(100, round(raw_score / max(85, max_score) * 100))
            m = MODES[slug]
            results.append({
                'mode': slug,
                'label': m['label'],
                'icon': m['icon'],
                'description': m['description'],
                'confidence': confidence,
            })

    return results[:5]  # top 5 at most


def handle_post_mode_intent(handler, body) -> bool:
    """POST /api/mode/intent — analyze user message and suggest best modes.

    Body: { message: str }
    Returns: { suggestions: [{ mode, label, icon, description, confidence }...] }
    """
    message = str(body.get('message', '')).strip()
    if not message:
        from api.helpers import bad
        return bad(handler, 'message is required')

    suggestions = detect_mode_intent(message)
    from api.helpers import j
    return j(handler, {
        'message': message,
        'suggestions': suggestions,
    })
