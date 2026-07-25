"""
TRACE-inspired Capability Diagnosis Engine for Daon Agent System.

Analyzes session trajectories via LLM to identify missing capabilities
and recommend skills, MCP servers, and prompt improvements.

No RL/GRPO — pure LLM-based analysis. MVP for validating the concept.
"""
import json
import logging
import os
import re
from typing import Any
from pathlib import Path

_logger = logging.getLogger(__name__)

# ── Capability taxonomy (TRACE-compatible labels) ──────────────────────────
CAPABILITY_TAXONOMY = [
    "API Reading",        # 공식 문서/레퍼런스를 참조하지 않고 추측
    "Planning",           # 작업 순서를 잘못 계획하여 후속 오류 발생
    "Tool Selection",     # 적절한 도구를 선택하지 못함
    "Error Recovery",     # 오류 발생 후 복구 전략 부재
    "Context Awareness",  # 워크스페이스/프로젝트 구조 이해 부족
    "Code Quality",       # 생성된 코드의 버그, 스타일, 비효율
    "Instruction Following",  # 사용자 지시를 정확히 따르지 못함
    "Self-Correction",    # 자신의 실수를 인지하고 수정하지 못함
    "Knowledge Gap",      # 도메인 지식 부족
    "Communication",      # 결과 보고/설명이 불충분
]

# ── LLM prompt template ────────────────────────────────────────────────────
DIAGNOSIS_SYSTEM_PROMPT = """You are a capability diagnosis engine inspired by TRACE (Capability-Targeted Agentic Training).
Analyze the given conversation trajectory between a user and an AI coding agent.

For each capability in the taxonomy, label it as one of:
- NA: Not applicable (this capability was not exercised in this session)
- PRESENT: The agent demonstrated adequate capability
- LACKING: The agent showed a clear gap in this capability

Then identify concrete, actionable recommendations.

Output ONLY valid JSON — no markdown, no explanation outside the JSON block."""

DIAGNOSIS_USER_PROMPT_TEMPLATE = """## Capability Taxonomy
{taxonomy}

## Conversation Trajectory
{trajectory}

## Output Schema
Return a JSON object with this exact structure:
{{
  "capabilities": [
    {{
      "name": "Capability Name (from taxonomy above)",
      "label": "LACKING" | "PRESENT" | "NA",
      "confidence": 0.0-1.0,
      "reason": "One-sentence evidence from the trajectory (in Korean)"
    }}
  ],
  "recommendations": {{
    "skills": ["skill_name"],
    "mcps": ["MCP server name or ID"],
    "references": ["documentation or reference names"],
    "prompt_improvements": ["concrete prompt improvements (in Korean)"]
  }},
  "summary": "One-sentence overall diagnosis (in Korean)"
}}

## Rules
1. Only label as LACKING when there is clear evidence in the trajectory.
2. Confidence should reflect how certain you are based on the evidence.
3. recommendations can be empty arrays if nothing is needed.
4. reason and summary MUST be in Korean.
5. Do NOT include markdown code fences. Output raw JSON only."""


def diagnose_session(session_id: str, model: str = None) -> dict:
    """
    Analyze a session's trajectory and return capability diagnosis.

    Args:
        session_id: The session ID to analyze
        model: Override the model used for diagnosis (default: from settings)

    Returns:
        dict with capabilities, recommendations, and summary
    """
    # 1. Load session
    from api.models import get_session
    session = get_session(session_id)
    if not session:
        return {"error": f"Session not found: {session_id}", "ok": False}

    # 2. Build trajectory text from messages
    trajectory = _build_trajectory(session)

    if not trajectory.strip():
        return {
            "ok": False,
            "error": "세션에 분석할 메시지가 없습니다.",
            "capabilities": [],
            "recommendations": {"skills": [], "mcps": [], "references": [], "prompt_improvements": []},
            "summary": "",
        }

    # 3. Get model config
    if model is None:
        model = _get_default_model()

    # 4. Call LLM for diagnosis
    try:
        result = _call_llm_for_diagnosis(trajectory, model)
        return result
    except Exception as e:
        _logger.error("Capability diagnosis LLM call failed: %s", e)
        return {
            "ok": False,
            "error": f"진단 중 오류 발생: {str(e)}",
            "capabilities": [],
            "recommendations": {"skills": [], "mcps": [], "references": [], "prompt_improvements": []},
            "summary": "",
        }


def _build_trajectory(session) -> str:
    """Build a readable trajectory text from session messages."""
    lines = []
    messages = getattr(session, 'messages', []) or []

    for i, msg in enumerate(messages):
        role = msg.get('role', 'unknown')
        content = msg.get('content', '')

        if role == 'user':
            # Truncate very long user messages
            if len(content) > 2000:
                content = content[:2000] + "...(truncated)"
            lines.append(f"[User #{i}] {content}")
        elif role == 'assistant':
            # Include tool calls if present
            tool_calls = msg.get('tool_calls', [])
            tc_summary = ""
            if tool_calls:
                tc_names = [tc.get('function', {}).get('name', '?') for tc in tool_calls[:10]]
                tc_summary = f" [도구 호출: {', '.join(tc_names)}]"
            if len(content) > 2000 and tool_calls:
                content = content[:500] + "...(truncated)"
            lines.append(f"[Assistant #{i}]{tc_summary} {content}")
        elif role == 'tool':
            tool_name = msg.get('name', 'tool')
            tool_content = msg.get('content', '')
            if len(tool_content) > 500:
                tool_content = tool_content[:500] + "...(truncated)"
            lines.append(f"[Tool: {tool_name}] {tool_content}")

    return "\n\n".join(lines)


def _get_default_model() -> str:
    """Get the default model from DAON settings."""
    try:
        from api.config import DEFAULT_MODEL
        return DEFAULT_MODEL
    except ImportError:
        return "minimax-m3"


def _load_llm_config() -> dict:
    """Load LLM provider configuration from settings."""
    try:
        from api.config import _load_settings
        settings = _load_settings()
        provider = settings.get('provider', 'minimax')
        api_key = settings.get('api_key', '')

        # Provider endpoint mapping
        endpoints = {
            'minimax': 'https://api.minimax.chat/v1/chat/completions',
            'openai': 'https://api.openai.com/v1/chat/completions',
            'anthropic': 'https://api.anthropic.com/v1/messages',
            'deepseek': 'https://api.deepseek.com/v1/chat/completions',
        }

        base_url = settings.get('api_base', endpoints.get(provider, endpoints['minimax']))
        if provider == 'anthropic':
            base_url = base_url or endpoints['anthropic']

        return {
            'provider': provider,
            'api_key': api_key,
            'base_url': base_url,
        }
    except Exception as e:
        _logger.warning("Failed to load LLM config: %s", e)
        return {
            'provider': 'minimax',
            'api_key': os.environ.get('MINIMAX_API_KEY', ''),
            'base_url': 'https://api.minimax.chat/v1/chat/completions',
        }


def _call_llm_for_diagnosis(trajectory: str, model: str) -> dict:
    """Call the LLM for capability diagnosis, with fallback to structured analysis."""
    import os
    import urllib.request
    import urllib.error

    config = _load_llm_config()
    api_key = config['api_key']
    base_url = config['base_url']

    # Try a heuristic approach if no API key is available (offline mode)
    if not api_key:
        _logger.warning("No API key configured — using heuristic diagnosis")
        return _heuristic_diagnosis(trajectory)

    # Build the prompt
    taxonomy_text = "\n".join(f"- {c}" for c in CAPABILITY_TAXONOMY)
    user_prompt = DIAGNOSIS_USER_PROMPT_TEMPLATE.format(
        taxonomy=taxonomy_text,
        trajectory=trajectory[:8000],  # Truncate to avoid token limits
    )

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": DIAGNOSIS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,  # Low temp for consistent analysis
        "max_tokens": 2000,
    }).encode('utf-8')

    # Make the API call
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }

    req = urllib.request.Request(base_url, data=payload, headers=headers, method='POST')

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else str(e)
        _logger.error("LLM API HTTP error %s: %s", e.code, error_body[:500])
        return _heuristic_diagnosis(trajectory)
    except Exception as e:
        _logger.error("LLM API call failed: %s", e)
        return _heuristic_diagnosis(trajectory)

    # Parse the response
    try:
        content = body.get('choices', [{}])[0].get('message', {}).get('content', '')
        # Strip markdown code fences if present
        content = re.sub(r'^```(?:json)?\s*', '', content.strip())
        content = re.sub(r'\s*```$', '', content.strip())
        result = json.loads(content)

        # Validate structure
        result["ok"] = True
        if "capabilities" not in result:
            result["capabilities"] = []
        if "recommendations" not in result:
            result["recommendations"] = {"skills": [], "mcps": [], "references": [], "prompt_improvements": []}
        if "summary" not in result:
            result["summary"] = ""

        # Filter to only LACKING capabilities for frontend display
        # but keep all for potential future use
        return result

    except (json.JSONDecodeError, KeyError, IndexError) as e:
        _logger.error("Failed to parse LLM diagnosis response: %s", e)
        _logger.debug("Raw content: %s", content[:500] if 'content' in dir() else 'N/A')
        return _heuristic_diagnosis(trajectory)


def _heuristic_diagnosis(trajectory: str) -> dict:
    """
    Heuristic/rule-based diagnosis fallback when LLM is unavailable.
    Analyzes trajectory for common failure patterns.
    """
    capabilities = []
    trajectory_lower = trajectory.lower()

    # Rule 1: Multiple consecutive tool errors → Error Recovery gap
    error_patterns = ['error', 'failed', 'exception', 'traceback', '오류', '실패', '에러']
    error_count = sum(1 for p in error_patterns if p in trajectory_lower)
    if error_count >= 3:
        capabilities.append({
            "name": "Error Recovery",
            "label": "LACKING",
            "confidence": min(0.6 + error_count * 0.1, 0.95),
            "reason": f"세션에서 {error_count}회 이상의 오류가 발생했으나 효과적인 복구가 이루어지지 않았습니다.",
        })

    # Rule 2: Tool calls without read/search first → API Reading gap
    tool_mentions = len(re.findall(r'\[Tool:', trajectory))
    search_mentions = len(re.findall(r'(search|read|find|검색|참조|문서)', trajectory_lower))
    if tool_mentions >= 5 and search_mentions == 0:
        capabilities.append({
            "name": "API Reading",
            "label": "LACKING",
            "confidence": 0.82,
            "reason": "도구를 여러 번 호출했지만 사전 검색이나 문서 참조 없이 진행되었습니다.",
        })

    # Rule 3: Many tool calls → Planning gap
    if tool_mentions >= 10:
        capabilities.append({
            "name": "Planning",
            "label": "LACKING",
            "confidence": min(0.5 + tool_mentions * 0.02, 0.85),
            "reason": f"도구를 {tool_mentions}회 호출했습니다 — 작업 계획이 비효율적일 수 있습니다.",
        })

    # Rule 4: Short responses → Communication gap
    assistant_msgs = [m for m in re.findall(r'\[Assistant.*?\]', trajectory)]
    if assistant_msgs:
        avg_len = sum(len(m) for m in assistant_msgs) / len(assistant_msgs)
        if avg_len < 100:
            capabilities.append({
                "name": "Communication",
                "label": "LACKING",
                "confidence": 0.7,
                "reason": "에이전트 응답이 매우 짧습니다 — 결과 설명이 충분하지 않을 수 있습니다.",
            })

    # Build recommendations from gaps
    recs = {"skills": [], "mcps": [], "references": [], "prompt_improvements": []}
    gap_names = {c["name"] for c in capabilities}

    if "API Reading" in gap_names:
        recs["mcps"].append("Context7")
        recs["skills"].append("official_docs_search")
        recs["prompt_improvements"].append("작업 전에 공식 문서나 레퍼런스를 먼저 검색하세요.")

    if "Planning" in gap_names:
        recs["skills"].append("sequential_thinking")
        recs["prompt_improvements"].append("복잡한 작업은 작은 단계로 나누어 순서를 계획한 후 실행하세요.")

    if "Error Recovery" in gap_names:
        recs["prompt_improvements"].append("오류 발생 시 원인을 분석하고 대안을 제시하세요. 같은 실수를 반복하지 마세요.")

    if "Communication" in gap_names:
        recs["prompt_improvements"].append("작업 완료 후 변경 사항과 결과를 명확히 요약하세요.")

    # Always provide some baseline capabilities
    all_taxonomy = set(CAPABILITY_TAXONOMY)
    mentioned = set(gap_names)
    for name in all_taxonomy - mentioned:
        # These weren't clearly lacking, mark as NA if not obviously present
        capabilities.append({
            "name": name,
            "label": "NA",
            "confidence": 0.5,
            "reason": "이 세션에서 이 역량의 부족 여부를 판단할 충분한 증거가 없습니다.",
        })

    summary = ""
    if gap_names:
        summary = f"주요 개선 필요 영역: {', '.join(sorted(gap_names))}"
    else:
        summary = "이 세션에서 뚜렷한 역량 부족이 감지되지 않았습니다."

    return {
        "ok": True,
        "capabilities": capabilities,
        "recommendations": recs,
        "summary": summary,
    }
