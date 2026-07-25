"""
TRACE-inspired Skill Unit Test Generator for Daon Agent System.

Reimagines TRACE's "Environment Generation" (GRPO training environments)
as lightweight unit tests that verify whether a skill actually works.

Instead of spinning up GPU gyms for RL training, we generate pragmatic
test scenarios: given a capability gap detected by the diagnosis engine,
what concrete prompt would verify that the recommended skill resolves it?

Architecture:
    Diagnosis → "API Reading is LACKING"
    ↓
    Test Generator → "Give agent this task WITHOUT docs.
                      Expected: agent must call search/read tools first.
                      Skill to test: official_docs_search"
    ↓
    User runs test → skill verified ✅  OR  skill needs improvement ❌
"""
import json
import logging
import re
from typing import Any

_logger = logging.getLogger(__name__)

# ── Test scenario templates per capability ─────────────────────────────────
# Each template: (scenario_description, expected_behavior, test_prompt, failure_indicator)

CAPABILITY_TEST_TEMPLATES = {
    "API Reading": [
        {
            "title": "문서 검색 없이 API 구현 요청",
            "description": "에이전트에게 생소한 라이브러리 사용을 요청한다. 문서 검색 도구를 먼저 호출해야 한다.",
            "test_prompt": "python에서 'httpx' 라이브러리를 사용해 비동기 GET 요청을 보내는 함수를 작성해줘. 문서는 제공하지 않는다.",
            "expected_behavior": "search_file/search_content 또는 web_search 도구를 먼저 호출한 후 코드를 작성한다.",
            "failure_indicator": "문서 참조 없이 바로 코드를 작성하거나 추측으로 API를 호출한다.",
            "target_skill": "auto-documenter",
            "difficulty": "medium",
        },
        {
            "title": "공식 레퍼런스 우회 확인",
            "description": "에이전트가 공식 문서가 아닌 커뮤니티 답변에 의존하는지 확인한다.",
            "test_prompt": "React 19에서 새로 추가된 'use()' 훅의 사용법을 알려줘.",
            "expected_behavior": "공식 React 문서 또는 RFC를 검색하여 정확한 사용법을 제공한다.",
            "failure_indicator": "블로그 글이나 StackOverflow 답변만 인용한다.",
            "target_skill": "official_docs_search",
            "difficulty": "easy",
        },
    ],
    "Planning": [
        {
            "title": "복잡한 다단계 작업 계획",
            "description": "여러 파일을 수정해야 하는 작업을 주고, 올바른 순서로 계획하는지 확인한다.",
            "test_prompt": "이 프로젝트에 다음 기능을 추가해줘:\n1. 사용자 로그인 API 엔드포인트 (POST /api/login)\n2. JWT 토큰 발급\n3. 미들웨어로 토큰 검증\n4. 보호된 라우트 (GET /api/me)\n데이터베이스는 SQLite를 사용하고, 비밀번호는 bcrypt로 해싱해.",
            "expected_behavior": "먼저 전체 작업 계획을 수립한 후, 의존성 순서대로 (DB 스키마 → bcrypt → JWT → API → 미들웨어) 단계별로 구현한다.",
            "failure_indicator": "순서를 고려하지 않고 무작위로 파일을 수정하거나, 의존성이 있는 코드를 먼저 작성한다.",
            "target_skill": "sequential-thinking",
            "difficulty": "hard",
        },
        {
            "title": "리팩토링 작업 순서",
            "description": "대규모 리팩토링 시 안전한 순서로 진행하는지 확인한다.",
            "test_prompt": "이 프로젝트의 모든 API 엔드포인트에서 에러 핸들링을 통일된 방식으로 리팩토링해줘. 현재는 각각 다른 방식으로 되어 있다.",
            "expected_behavior": "1) 현재 상태 분석 → 2) 공통 에러 핸들러 설계 → 3) 한 엔드포인트씩 점진적 마이그레이션 → 4) 테스트.",
            "failure_indicator": "모든 파일을 한 번에 수정하거나, 테스트 없이 변경을 완료한다.",
            "target_skill": "sequential-thinking",
            "difficulty": "medium",
        },
    ],
    "Tool Selection": [
        {
            "title": "적절한 도구 선택 확인",
            "description": "파일 검색이 필요한 작업에서 적절한 도구를 선택하는지 확인한다.",
            "test_prompt": "이 프로젝트에서 'config'라는 단어가 포함된 모든 Python 파일을 찾아서, 각 파일의 설정 키 목록을 알려줘.",
            "expected_behavior": "search_content 도구로 'config' 패턴을 검색한 후, 찾은 파일들을 read_file로 읽는다.",
            "failure_indicator": "잘못된 도구를 사용하거나, 프로젝트 구조를 파악하지 않고 추측한다.",
            "target_skill": "full-output",
            "difficulty": "easy",
        },
    ],
    "Error Recovery": [
        {
            "title": "의도적 오류 복구 테스트",
            "description": "의도적으로 오류가 발생하는 상황을 만들고, 에이전트가 복구하는지 확인한다.",
            "test_prompt": "이 Python 파일을 실행해서 결과를 알려줘:\n\nimport nonexistent_module\nprint('hello')\n\n(위 코드를 test_broken.py로 저장한 후 실행해줘)",
            "expected_behavior": "ModuleNotFoundError를 감지하고, 존재하지 않는 모듈임을 보고한 후 대안을 제시한다.",
            "failure_indicator": "오류 메시지를 무시하거나, 원인 분석 없이 포기한다.",
            "target_skill": "self-reflection",
            "difficulty": "easy",
        },
    ],
    "Context Awareness": [
        {
            "title": "워크스페이스 구조 이해 확인",
            "description": "에이전트가 현재 프로젝트의 디렉토리 구조를 제대로 파악하는지 확인한다.",
            "test_prompt": "이 프로젝트에서 API 라우트 핸들러가 정의된 파일 목록을 알려주고, 각각 어떤 기능을 담당하는지 설명해줘.",
            "expected_behavior": "list_files로 프로젝트를 탐색한 후, api/routes/ 디렉토리의 파일들을 읽고 분석한다.",
            "failure_indicator": "프로젝트 탐색 없이 일반적인 답변을 하거나, 잘못된 파일 경로를 참조한다.",
            "target_skill": "auto-documenter",
            "difficulty": "medium",
        },
    ],
    "Communication": [
        {
            "title": "결과 보고 품질 확인",
            "description": "작업 완료 후 변경 사항을 명확히 요약하는지 확인한다.",
            "test_prompt": "README.md 파일에 프로젝트 설명을 3줄 추가하고, 방금 무엇을 변경했는지 설명해줘.",
            "expected_behavior": "변경 전/후를 명확히 대조하고, 추가된 내용을 구체적으로 보고한다.",
            "failure_indicator": "'완료했습니다'라고만 말하고 구체적인 변경 내용을 설명하지 않는다.",
            "target_skill": "full-output",
            "difficulty": "easy",
        },
    ],
}

# ── Generic templates for any capability ──────────────────────────────────
GENERIC_TEST_TEMPLATE = {
    "title": "일반 역량 검증",
    "description": "주어진 역량이 실제로 작동하는지 확인하는 기본 테스트",
    "test_prompt": "",
    "expected_behavior": "",
    "failure_indicator": "",
    "difficulty": "medium",
}


def generate_skill_tests(diagnosis_result: dict) -> dict:
    """
    Generate skill unit tests from a capability diagnosis result.

    Args:
        diagnosis_result: Output from capability_diagnosis.diagnose_session()
                          Expected keys: capabilities, recommendations, summary

    Returns:
        dict with:
            tests: [list of test scenarios]
            summary: overview of what will be tested
    """
    capabilities = diagnosis_result.get("capabilities", [])
    recommendations = diagnosis_result.get("recommendations", {})

    # Filter LACKING capabilities only
    lacking = [c for c in capabilities if c.get("label") == "LACKING"]
    lacking.sort(key=lambda c: c.get("confidence", 0), reverse=True)

    tests = []
    tested_capabilities = set()

    for cap in lacking:
        cap_name = cap.get("name", "")
        confidence = cap.get("confidence", 0)

        if cap_name in CAPABILITY_TEST_TEMPLATES:
            for template in CAPABILITY_TEST_TEMPLATES[cap_name]:
                test = dict(template)  # Copy
                test["capability"] = cap_name
                test["capability_confidence"] = confidence
                test["capability_reason"] = cap.get("reason", "")
                test["id"] = _make_test_id(cap_name, template["title"])
                tests.append(test)
                tested_capabilities.add(cap_name)

    # If a recommended skill doesn't have a specific template, generate a generic one
    for skill_name in recommendations.get("skills", []):
        already_covered = any(
            t.get("target_skill") == skill_name for t in tests
        )
        if not already_covered:
            tests.append({
                **GENERIC_TEST_TEMPLATE,
                "title": f"스킬 검증: {skill_name}",
                "description": f"'{skill_name}' 스킬이 제대로 작동하는지 확인한다.",
                "test_prompt": f"이전에 '{skill_name}' 스킬이 필요했던 작업을 다시 시도해보고, 이번에는 스킬이 활성화되었는지 확인한다.",
                "expected_behavior": f"{skill_name} 스킬이 활성화되어 작업 품질이 향상된다.",
                "failure_indicator": f"스킬이 활성화되지 않았거나, 이전과 동일한 실패 패턴이 반복된다.",
                "capability": "General",
                "capability_confidence": 0.5,
                "capability_reason": "추천된 스킬 검증",
                "target_skill": skill_name,
                "id": _make_test_id("General", skill_name),
                "difficulty": "easy",
            })

    # Build summary
    lacking_names = [c.get("name", "") for c in lacking]
    summary = ""

    if lacking_names:
        summary = f"총 {len(tests)}개의 테스트가 생성되었습니다. "
        summary += f"부족한 역량: {', '.join(lacking_names[:3])}"
        if len(lacking_names) > 3:
            summary += f" 외 {len(lacking_names) - 3}개"

    return {
        "ok": True,
        "tests": tests,
        "total_tests": len(tests),
        "tested_capabilities": sorted(tested_capabilities),
        "summary": summary,
    }


def _make_test_id(capability: str, title: str) -> str:
    """Generate a stable test ID."""
    import hashlib
    raw = f"{capability}:{title}"
    return hashlib.md5(raw.encode()).hexdigest()[:8]


def run_skill_test(test_id: str, session_id: str, workspace: str, model: str = None) -> dict:
    """
    Execute a single skill test against a running agent session.

    Args:
        test_id: The test ID to run
        session_id: The session to run the test in (will send test prompt)
        workspace: The workspace path
        model: Model to use

    Returns:
        dict with test result (pass/fail + observations)
    """
    # This is a stub — actual execution requires sending the test_prompt
    # through the agent stream and observing tool call patterns.
    # For MVP, we return the test definition so the UI can prompt the user.

    return {
        "ok": True,
        "status": "ready",
        "message": "테스트가 준비되었습니다. UI에서 '테스트 실행'을 클릭하면 에이전트에게 테스트 프롬프트가 전송됩니다.",
        "note": "Full test execution requires agent stream integration — MVP returns test definition for manual execution.",
    }
