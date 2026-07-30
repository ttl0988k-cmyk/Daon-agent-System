"""
CEO Clarification Phase — Claude Code-style interview before harness execution.

Flow:
  1. CEO analyzes task ambiguity → generates 1~3 targeted questions
  2. User answers → CEO evaluates sufficiency
  3. Repeat up to MAX_TURNS (3) or until CEO says "enough"
  4. Build enriched task description from Q&A context

The job enters 'clarifying' status; the frontend polls and renders question cards.
User answers via POST /api/dynamic/answer/{run_id}.
"""

import json
import threading
from typing import Optional

from api.dynamic.direct_calls import _call_direct
from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)

MAX_CLARIFICATION_TURNS = 3

# ─── In-memory clarification state per run_id ───
_CLARIFICATION_STATE: dict[str, dict] = {}
_CLARIFICATION_LOCK = threading.Lock()


def _get_state(run_id: str) -> Optional[dict]:
    with _CLARIFICATION_LOCK:
        return _CLARIFICATION_STATE.get(run_id)


def _set_state(run_id: str, state: dict):
    with _CLARIFICATION_LOCK:
        _CLARIFICATION_STATE[run_id] = state


def _clear_state(run_id: str):
    with _CLARIFICATION_LOCK:
        _CLARIFICATION_STATE.pop(run_id, None)


# ─── LLM Prompts ───

_ANALYZE_SYSTEM = """\
당신은 DAON 시스템의 CEO 에이전트입니다.
사용자가 요청한 작업을 분석하여, 작업을 정확히 수행하기 위해 추가로 확인이 필요한 사항을 질문합니다.

규칙:
- 작업이 이미 충분히 구체적이면 질문 없이 바로 "ENOUGH"라고 응답합니다.
- 질문은 최대 3개까지, 핵심만 간결하게 묻습니다.
- 질문은 번호 매긴 목록으로 작성합니다.
- 한국어로 응답합니다.

응답 형식 (반드시 JSON):
{
  "needs_clarification": true/false,
  "questions": ["질문1", "질문2", ...],
  "reasoning": "왜 이 질문이 필요한지 한 줄"
}

작업이 명확한 경우:
{
  "needs_clarification": false,
  "questions": [],
  "reasoning": "이미 충분히 구체적"
}
"""

_EVALUATE_SYSTEM = """\
당신은 DAON 시스템의 CEO 에이전트입니다.
사용자의 답변을 평가하여, 작업을 시작하기에 충분한 정보가 모였는지 판단합니다.

규칙:
- 최대 {max_turns}턴까지 질문할 수 있습니다. 현재 {current_turn}턴째입니다.
- 정보가 충분하면 "ENOUGH"로 판단하고 enriched_task를 작성합니다.
- 아직 부족하면 추가 질문을 생성합니다 (최대 3개).
- enriched_task는 원본 작업 + Q&A에서 얻은 맥락을 합친 상세 작업 설명입니다.
- 한국어로 응답합니다.

응답 형식 (반드시 JSON):
{{
  "needs_clarification": true/false,
  "questions": ["추가 질문1", ...],
  "enriched_task": "Q&A 맥락이 포함된 상세 작업 설명 (needs_clarification=false일 때 필수)",
  "reasoning": "판단 근거 한 줄"
}}
"""


def _parse_json_response(raw: str) -> dict:
    """Extract JSON from LLM response (handles code fences)."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return {"needs_clarification": False, "questions": [], "reasoning": "parse error — proceeding"}


def analyze_task(task: str, preferred_model: str = None) -> dict:
    """Phase 1: Analyze task ambiguity and generate initial questions.

    Returns:
        {"needs_clarification": bool, "questions": [...], "reasoning": str}
    """
    prompt = f"사용자 작업 요청:\n\"\"\"\n{task}\n\"\"\"\n\n이 작업을 정확히 수행하기 위해 추가 확인이 필요한가요?"
    try:
        raw = _call_direct(prompt, _ANALYZE_SYSTEM, preferred_model=preferred_model)
        result = _parse_json_response(raw)
        _log.info("Clarification analysis: needs=%s, questions=%d",
                  result.get("needs_clarification"), len(result.get("questions", [])))
        return result
    except Exception as e:
        _log.warning("Clarification analysis failed: %s — skipping", e)
        return {"needs_clarification": False, "questions": [], "reasoning": str(e)}


def evaluate_answers(task: str, qa_history: list[dict], current_turn: int,
                     preferred_model: str = None) -> dict:
    """Phase 2+: Evaluate user answers and decide whether to continue or proceed.

    Args:
        task: Original task text
        qa_history: List of {"questions": [...], "answers": [...]} dicts
        current_turn: Current turn number (1-based)

    Returns:
        {"needs_clarification": bool, "questions": [...], "enriched_task": str, "reasoning": str}
    """
    system = _EVALUATE_SYSTEM.format(max_turns=MAX_CLARIFICATION_TURNS, current_turn=current_turn)

    # Build Q&A context
    qa_text = ""
    for i, turn in enumerate(qa_history, 1):
        qa_text += f"\n--- 턴 {i} ---\n"
        for q, a in zip(turn.get("questions", []), turn.get("answers", [])):
            qa_text += f"Q: {q}\nA: {a}\n"

    prompt = (
        f"원본 작업:\n\"\"\"\n{task}\n\"\"\"\n\n"
        f"Q&A 이력:\n{qa_text}\n\n"
        f"이제 작업을 시작하기에 충분한가요? 아니면 추가 질문이 필요한가요?"
    )

    try:
        raw = _call_direct(prompt, system, preferred_model=preferred_model)
        result = _parse_json_response(raw)
        _log.info("Clarification evaluate (turn %d): needs=%s",
                  current_turn, result.get("needs_clarification"))
        return result
    except Exception as e:
        _log.warning("Clarification evaluate failed: %s — proceeding", e)
        return {"needs_clarification": False, "questions": [], "enriched_task": task, "reasoning": str(e)}


def build_enriched_task(task: str, qa_history: list[dict]) -> str:
    """Fallback: build enriched task from Q&A without LLM (if evaluate didn't return one)."""
    parts = [f"## 원본 요청\n{task}\n"]
    for i, turn in enumerate(qa_history, 1):
        parts.append(f"## 추가 확인 {i}")
        for q, a in zip(turn.get("questions", []), turn.get("answers", [])):
            parts.append(f"- **{q}**\n  → {a}")
    return "\n\n".join(parts)


# ─── Job-level clarification orchestration ───

def init_clarification(run_id: str, task: str, preferred_model: str = None) -> dict:
    """Start clarification for a job. Returns the initial state.

    State structure:
    {
        "run_id": str,
        "task": str,
        "preferred_model": str,
        "turn": int,
        "qa_history": [{"questions": [...], "answers": [...]}],
        "current_questions": [...],
        "status": "analyzing" | "waiting" | "done",
        "enriched_task": str | None,
        "answer_event": threading.Event,  # signaled when user answers
        "user_answers": [...]  # filled by answer endpoint
    }
    """
    state = {
        "run_id": run_id,
        "task": task,
        "preferred_model": preferred_model,
        "turn": 0,
        "qa_history": [],
        "current_questions": [],
        "status": "analyzing",
        "enriched_task": None,
        "answer_event": threading.Event(),
        "user_answers": [],
    }
    _set_state(run_id, state)

    # Analyze task
    analysis = analyze_task(task, preferred_model)

    if not analysis.get("needs_clarification") or not analysis.get("questions"):
        # Task is clear enough — skip clarification
        state["status"] = "done"
        state["enriched_task"] = task
        _log.info("Task '%s' is clear — skipping clarification", task[:50])
        return state

    # Enter waiting state with questions
    state["turn"] = 1
    state["current_questions"] = analysis["questions"][:3]
    state["status"] = "waiting"
    state["qa_history"].append({"questions": state["current_questions"], "answers": []})
    _log.info("Clarification needed for '%s': %d questions", task[:50], len(state["current_questions"]))
    return state


def submit_answers(run_id: str, answers: list[str]) -> dict:
    """Called by the API endpoint when user submits answers.

    Signals the waiting thread and returns updated state.
    """
    state = _get_state(run_id)
    if state is None:
        return {"ok": False, "error": "No clarification session for this run_id"}

    if state["status"] != "waiting":
        return {"ok": False, "error": f"Not waiting for answers (status={state['status']})"}

    # Store answers and signal
    state["user_answers"] = answers
    state["answer_event"].set()
    return {"ok": True}


def abort_clarification(run_id: str) -> bool:
    """사용자가 하네스를 취소했을 때 답변 대기를 즉시 해제한다.

    wait_for_answers()에서 블록 중인 스레드를 깨우고, aborted 플래그를 세워
    clarification 루프가 더 이상 질문을 이어가지 않도록 한다.
    """
    state = _get_state(run_id)
    if state is None:
        return False
    state["aborted"] = True
    try:
        state["answer_event"].set()
    except Exception:
        pass
    return True


def wait_for_answers(run_id: str, timeout: float = 300.0) -> Optional[list[str]]:
    """Block until user answers or timeout. Returns answers or None."""
    state = _get_state(run_id)
    if state is None:
        return None

    signaled = state["answer_event"].wait(timeout=timeout)
    if not signaled:
        return None

    # 취소로 인해 깨어난 경우 답변 없이 None 반환 → 루프 종료
    if state.get("aborted"):
        return None

    answers = state["user_answers"]
    # Reset for next turn
    state["answer_event"].clear()
    state["user_answers"] = []
    return answers


def run_clarification_loop(run_id: str, task: str, preferred_model: str = None,
                           log_callback=None) -> str:
    """Full clarification loop. Blocks until done or max turns.

    Returns the enriched task string.
    """
    state = init_clarification(run_id, task, preferred_model)

    if state["status"] == "done":
        _clear_state(run_id)
        return state["enriched_task"] or task

    # Loop: wait for answers → evaluate → maybe ask more
    while state["status"] == "waiting" and state["turn"] <= MAX_CLARIFICATION_TURNS:
        if log_callback:
            log_callback("CEO", f"💬 확인 질문 (턴 {state['turn']}): " +
                         " / ".join(state["current_questions"]), "clarifying")

        # Wait for user answers (blocks thread)
        answers = wait_for_answers(run_id, timeout=300.0)
        if answers is None:
            # Timeout — proceed with what we have
            _log.warning("Clarification timeout for run %s — proceeding", run_id)
            break

        # Record answers
        current_turn_data = state["qa_history"][-1]
        current_turn_data["answers"] = answers

        # Evaluate
        evaluation = evaluate_answers(task, state["qa_history"], state["turn"], preferred_model)

        if not evaluation.get("needs_clarification") or state["turn"] >= MAX_CLARIFICATION_TURNS:
            # Done — use enriched task
            enriched = evaluation.get("enriched_task", "")
            if not enriched:
                enriched = build_enriched_task(task, state["qa_history"])
            state["status"] = "done"
            state["enriched_task"] = enriched
            if log_callback:
                log_callback("CEO", "✅ 의도 파악 완료 — 작업 시작합니다", "success")
            break
        else:
            # More questions
            state["turn"] += 1
            state["current_questions"] = evaluation.get("questions", [])[:3]
            state["qa_history"].append({"questions": state["current_questions"], "answers": []})
            state["status"] = "waiting"

    # Fallback
    if state["status"] != "done":
        enriched = build_enriched_task(task, state["qa_history"])
        state["enriched_task"] = enriched

    result = state["enriched_task"] or task
    _clear_state(run_id)
    return result


def get_clarification_status(run_id: str) -> Optional[dict]:
    """Get current clarification status for polling (without internal threading objects)."""
    state = _get_state(run_id)
    if state is None:
        return None
    return {
        "status": state["status"],
        "turn": state["turn"],
        "questions": state["current_questions"],
        "qa_history": state["qa_history"],
    }
