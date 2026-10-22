"""
Boardroom Routes — 회의실 오케스트레이터 (라온 사회자 구조).

역할 분담 (2026-10-15 대표님 확정):
  * 라온은 회의에 '참석하지 않는다' — 라온은 사회자 겸 통합자다.
  * 빌·셜록·토니·프라다·다온 + CLI 워커(코덱스/클로드)만 발언한다.
  * 1차 토론 → 라온이 취합 → 이견/문제점이 있으면 해당 에이전트에게 재질의
    → 합의에 이를 때까지 반복(최대 MAX_ROUNDS).
  * 결론은 라온이 채팅창에서 대표님께 보고하고, 승인 뒤 작업장으로 넘어간다.

진입 경로 두 가지:
  A) POST /api/boardroom/broadcast   — 회의실 탭에서 직접 소집
  B) convene_boardroom 도구(라온)     — 채팅에서 "회의해봐" 로 소집 (라온 턴 안에서 실행)

핵심 함수는 run_boardroom() 하나이며, A/B 모두 이것을 호출한다.
A는 emit 콜백으로 SSE에 실시간 중계하고, B는 반환값(결론)을 라온에게 돌려준다.

SSE 이벤트:
  boardroom_slot        {slot, speaker, kind, status, round}
  boardroom_reply       {slot, speaker, kind, status, text, model, elapsed, round}
  boardroom_round_start {round, slots}          — 라운드 시작
  boardroom_round_done  {round, replies}        — 라운드 전원 응답 완료
  boardroom_moderation  {round, verdict}        — 라온 취합 결과(합의 여부/재질의)
  boardroom_conclusion  {summary, points, disagreements, next_steps, rounds, sessions}
  boardroom_done        {ok, task, sessions, conclusion}
"""

import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


# (slot_no, label, kind, key) — 라온(구 01)은 사회자이므로 참석자 목록에서 제외.
#   kind='agent'  -> collaborator.execute_agent_task(key, task, model=...)
#   kind='worker' -> herdr_manager.execute_worker_task(name=key, prompt=task, model=...)
BOARDROOM_SLOTS = [
    ("02", "빌 (개발)", "agent", "bill"),
    ("03", "셜록 (검수)", "agent", "sherlock"),
    ("04", "토니 (기획)", "agent", "tony"),
    ("05", "프라다 (디자인)", "agent", "prada"),
    ("06", "다온 (응대)", "agent", "daon"),
    ("07", "코덱스 워커", "worker", "worker-codex"),
    ("08", "클로드 워커", "worker", "worker-claude"),
]

# 사회자 슬롯 — 프론트에서 '라온(사회자)'로 표시한다. 발언은 하지 않는다.
MODERATOR_SLOT = "01"
MODERATOR_LABEL = "라온 (사회자·통합)"

SLOT_TIMEOUT = 420     # 슬롯 1회 발언 예산
SUMMARY_TIMEOUT = 300  # 라온 취합/재질의 판단 예산
MAX_ROUNDS = 3         # 1차 + 재질의 최대 2회

# 취합 프롬프트에 넣을 슬롯당 발언 길이 상한(컨텍스트 폭주 방지).
SUMMARY_SLOT_CHARS = 2500


def _clip(text: str, n: int = SUMMARY_SLOT_CHARS) -> str:
    t = (text or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


def _meeting_brief(agenda: str) -> str:
    """회의실 슬롯용 기본 브리핑 — '발언' 단계임을 못박는다.

    각 슬롯은 도구를 가진 완전한 에이전트라, 제약이 없으면 브라우저/터미널을
    오래 돌다가 SLOT_TIMEOUT을 넘기고, 느린 응답은 SSE 연결이 끊겨 유실된다.
    회의실은 논의 무대이고, 실제 작업은 회의 뒤 작업장에서 한다.
    """
    return (
        "[회의실 브리핑] 지금은 회의실 토론 단계입니다.\n"
        "실제 파일 생성·코드 작성·빌드 같은 실행은 회의가 끝난 뒤 열리는 '작업장'에서 합니다.\n"
        "따라서 브라우저/터미널/파일 도구 사용은 꼭 필요한 경우로 최소화하고, 아래 안건에 대해\n"
        "네 전문 분야 관점의 핵심 의견·제안·리스크를 3~6문장으로 간결하게 발언하세요.\n\n"
        f"안건: {agenda}"
    )


def _slot_prompt(agenda: str, round_no: int, prior: dict, question: str = "") -> str:
    """라운드별 슬롯 프롬프트. 2라운드부터는 1차 발언 요약 + 사회자 재질의를 붙인다."""
    base = _meeting_brief(agenda)
    if round_no <= 1:
        return base

    lines = ["", "", "[1차 라운드 발언 요약]"]
    for _slot in sorted(prior or {}):
        row = prior.get(_slot) or {}
        lines.append(f"- {row.get('speaker', '?')}: {_clip(row.get('text', ''), 800)}")
    lines.append("")
    lines.append("[사회자(라온)의 재질의]")
    lines.append(question or "1차 발언에서 드러난 이견과 빠진 쟁점을 좁혀 주세요.")
    lines.append("")
    lines.append(
        "위 재질의에 대해 이전 발언을 그대로 반복하지 말고, 쟁점에 대한 네 결론을 명확히 밝히세요. "
        "다른 슬롯과 입장이 다르면 무엇이 다른지 근거와 함께 말하세요."
    )
    return base + "\n".join(lines)


def _resolve_slot_model(models: dict, slot, key) -> str:
    """Pick the model override for a slot. Accepts keys by slot number ('02')
    or by agent/worker key ('bill', 'worker-codex'). Returns '' when unset."""
    if not isinstance(models, dict):
        return ""
    val = models.get(slot) or models.get(key) or ""
    return str(val).strip()


# ── 슬롯 실행 ────────────────────────────────────────────────────────────────

def _run_agent_slot(emit, slot, label, agent_key, prompt, model=None, sink=None, round_no=1):
    """Run one static-agent slot and emit its reply.

    sink: optional shared dict — records this slot's session id and reply text so
    the moderator can aggregate and the workshop hand-off can resume sessions.
    """
    t0 = time.time()
    emit("boardroom_slot", {"slot": slot, "speaker": label, "kind": "agent",
                            "status": "running", "model": model or "", "round": round_no})
    ok = False
    text = ""
    session_id = ""
    try:
        from api.collaborator import execute_agent_task
        # force_new_session=True keeps these meeting turns out of the agent's
        # regular conversation threads (esp. raon, whose most-recent session is
        # the live chat session the user is typing in).
        # no_tools=True: boardroom slots are opinion/briefing only — prevent
        # "Continue now" nudge from triggering real work (file writes, browser
        # ops) during the meeting.  This fixes the bug where Prada's slot
        # spent 287 s executing actual work instead of emitting a reply
        # immediately after its opinion (root cause: "확인해야" triggered
        # _looks_like_intermediate_ack → Continue-now nudge → tool execution).
        res = execute_agent_task(
            agent_key, prompt, timeout=SLOT_TIMEOUT, force_new_session=True,
            model=(model or None), no_tools=True,
        )
        ok = bool(res.get("ok"))
        text = (res.get("output") if ok else (res.get("error") or "작업 미완료")) or ""
        session_id = res.get("session_id") or ""
    except Exception as e:  # noqa: BLE001
        logger.exception("boardroom agent slot failed: %s", agent_key)
        ok, text = False, f"{type(e).__name__}: {e}"

    elapsed = round(time.time() - t0, 1)
    emit("boardroom_reply", {
        "slot": slot, "speaker": label, "kind": "agent",
        "status": "done" if ok else "error",
        "text": text, "model": model or "", "elapsed": elapsed, "round": round_no,
        "session_id": session_id,
    })
    if sink is not None:
        sink[slot] = {
            "slot": slot, "speaker": label, "kind": "agent", "key": agent_key,
            "profile": agent_key, "session_id": session_id, "model": model or "",
            "ok": ok, "text": text, "elapsed": elapsed, "round": round_no,
        }


def _run_worker_slot(emit, slot, label, worker_key, prompt, model=None, sink=None, round_no=1):
    """Run one CLI-worker slot and emit its reply.

    sink: records the worker so the workshop hand-off can open a worker pane
    (workers have no chat session id; the multi-view opens them by worker name).
    """
    t0 = time.time()
    emit("boardroom_slot", {"slot": slot, "speaker": label, "kind": "worker",
                            "status": "running", "model": model or "", "round": round_no})
    ok = False
    text = ""
    try:
        from api.managers.herdr_manager import herdr_manager
        res = herdr_manager.execute_worker_task(
            name=worker_key, prompt=prompt, timeout=SLOT_TIMEOUT, model=(model or None)
        )
        ok = bool(res.get("ok"))
        text = (res.get("result") if ok else (res.get("error") or res.get("status") or "워커 미완료")) or ""
    except Exception as e:  # noqa: BLE001
        logger.exception("boardroom worker slot failed: %s", worker_key)
        ok, text = False, f"{type(e).__name__}: {e}"

    elapsed = round(time.time() - t0, 1)
    emit("boardroom_reply", {
        "slot": slot, "speaker": label, "kind": "worker",
        "status": "done" if ok else "error",
        "text": text, "model": model or "", "elapsed": elapsed, "round": round_no,
        "session_id": "",
    })
    if sink is not None:
        sink[slot] = {
            "slot": slot, "speaker": label, "kind": "worker", "key": worker_key,
            "profile": worker_key, "session_id": "", "model": model or "",
            "ok": ok, "text": text, "elapsed": elapsed, "round": round_no,
        }


# ── 라온 사회자: 취합 + 합의 판정 ─────────────────────────────────────────────

_MODERATOR_SYS = (
    "너는 다온 시스템의 총괄기획자 '라온'이다. 회의실에 참석하지 않고 사회자·통합자로만 일한다.\n"
    "너의 일은 여러 에이전트의 발언을 취합해 (1) 합의된 결론, (2) 남은 이견, "
    "(3) 추가로 물어봐야 할 쟁점을 판단하는 것이다.\n\n"
    "반드시 아래 JSON 하나만 출력한다. 설명·인사말·마크다운 코드펜스를 붙이지 않는다.\n"
    "도구를 호출하지 않는다. 파일을 만들지 않는다. 오직 판단과 텍스트만 낸다.\n"
    "{\n"
    '  "consensus": true | false,\n'
    '  "summary": "현재까지의 결론을 3~6문장으로",\n'
    '  "points": ["합의된 핵심 1", "..."],\n'
    '  "disagreements": ["아직 갈리는 쟁점 1", "..."],\n'
    '  "requeries": { "슬롯번호": "그 슬롯에 다시 물을 구체적 질문" },\n'
    '  "next_steps": ["합의 후 실행할 작업 1", "..."]\n'
    "}\n\n"
    "consensus 규칙: 쟁점이 실행을 막을 만큼 크지 않고 방향이 모였다면 true. "
    "핵심 쟁점이 남아 다시 물어야 하면 false. "
    "disagreements가 비었는데도 false로 두지 말 것. "
    "requeries는 consensus=false일 때만 채우고, 상충하는 슬롯 또는 침묵한 슬롯을 지정한다."
)


def _extract_json(text: str) -> dict:
    """라온 응답에서 JSON 오브젝트를 관대하게 추출한다.

    🚨 2026-10-21: 응답에 JSON이 여러 개 섞일 수 있다(예: 안건 내용 JSON +
    취합 JSON). 종전에는 첫 `{`~마지막 `}` 한 덩어리만 시도해, 안건 내용 JSON을
    잡아 summary가 빈 문자열이 되는 사고가 있었다. 이제 취합 스키마 키
    (summary/consensus/points)를 가진 오브젝트를 우선 선택한다.
    """
    t = (text or "").strip()
    if not t:
        return {}
    _SCHEMA_KEYS = ("summary", "consensus", "points", "disagreements", "next_steps")

    def _pick(obj):
        """취합 스키마 키가 하나라도 있으면 그 오브젝트를 채택."""
        return isinstance(obj, dict) and any(k in obj for k in _SCHEMA_KEYS)

    # 코드펜스 제거 (여러 개 존재 가능 → 전부 제거)
    t2 = re.sub(r"```(?:json)?", "", t).replace("```", "").strip()
    try:
        obj = json.loads(t2)
        if isinstance(obj, dict) and _pick(obj):
            return obj
    except Exception:  # noqa: BLE001
        pass

    # 응답 내 모든 { ... } 후보를 균형 스캔으로 모은 뒤, 스키마 일치 우선.
    cands: list = []
    depth, start = 0, -1
    for idx, ch in enumerate(t2):
        if ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    cands.append(t2[start:idx + 1])
                    start = -1
    parsed = []
    for c in cands:
        try:
            o = json.loads(c)
            if isinstance(o, dict):
                parsed.append(o)
        except Exception:  # noqa: BLE001
            continue
    for o in parsed:
        if _pick(o):
            return o
    if parsed:
        return parsed[0]
    return {}


def _moderate(agenda: str, round_no: int, replies: dict, emit=None) -> dict:
    """라온이 이번 라운드 발언을 취합해 판정한다. 실패해도 예외를 올리지 않는다."""
    blocks = [f"[안건] {agenda}", f"[라운드] {round_no}"]
    for slot in sorted(replies or {}):
        row = replies.get(slot) or {}
        if not row.get("text"):
            continue
        blocks.append(
            f"[{row.get('slot')} {row.get('speaker')}]\n{_clip(row.get('text'))}"
        )
    # 🚨 2026-10-21: 사회자 스키마를 프롬프트에 반드시 싣는다.
    #    종전에는 _MODERATOR_SYS 상수만 정의하고 전달하지 않아(죽은 코드),
    #    라온이 취합 스키마를 모른 채 '안건 내용에 대한 JSON'(예: cardLayout)을
    #    출력했고 → _extract_json이 그걸 잡아 summary가 빈 문자열이 됐다.
    blocks.append(
        "\n" + _MODERATOR_SYS +
        "\n\n위 발언들을 취합해 위 JSON 스키마 그대로 하나만 출력한다."
    )
    prompt = "\n\n".join(blocks)

    raw = ""
    try:
        from api.collaborator import execute_agent_task
        res = execute_agent_task(
            "raon", prompt, timeout=SUMMARY_TIMEOUT, force_new_session=True,
            no_tools=True,   # 취합은 판단만 — 도구 호출로 지연/오염되지 않게
        )
        ok = bool(res.get("ok"))
        raw = (res.get("output") if ok else "") or ""
    except Exception:  # noqa: BLE001
        logger.exception("boardroom moderation failed")

    verdict = _extract_json(raw)
    if not verdict:
        # 파싱 실패 폴백 — 결론 텍스트만 살리고 합의로 간주해 루프를 끝낸다.
        verdict = {
            "consensus": True,
            "summary": _clip(raw, 1500) or "취합 결과를 해석하지 못했습니다.",
            "points": [], "disagreements": [], "requeries": {}, "next_steps": [],
            "parse_failed": True,
        }
    verdict.setdefault("consensus", True)
    verdict.setdefault("summary", "")
    # 🚨 2026-10-21: 스키마는 맞는데 summary만 빈 경우가 있었다(사회자가 엉뚱한
    #    JSON을 낸 뒤 setdefault로 빈 문자열이 고정됨). raw 텍스트로 반드시 메운다.
    if not str(verdict.get("summary") or "").strip():
        verdict["summary"] = _clip(raw, 1500) or "취합 결과를 해석하지 못했습니다."
    for k in ("points", "disagreements", "next_steps"):
        if not isinstance(verdict.get(k), list):
            verdict[k] = []
    if not isinstance(verdict.get("requeries"), dict):
        verdict["requeries"] = {}
    verdict["round"] = round_no
    if emit:
        emit("boardroom_moderation", {"round": round_no, "verdict": verdict})
    return verdict


# ── 오케스트레이터 본체 ───────────────────────────────────────────────────────

def run_boardroom(agenda: str, selected_slots: list, models: dict = None,
                  emit=None, max_rounds: int = MAX_ROUNDS) -> dict:
    """회의를 끝까지 진행하고 결론을 반환한다.

    agenda: 정리된 안건 (라온이 채팅에서 정리해 넘긴 문장)
    selected_slots: BOARDROOM_SLOTS 튜플 리스트 (참석자)
    models: 슬롯별 모델 오버라이드 {"02": "glm-5.3-flash", ...}
    emit: SSE 중계 콜백 (없으면 조용히 실행 — 도구 경로)
    max_rounds: 1차 + 재질의 최대 라운드

    반환: {
      "ok", "agenda", "rounds", "consensus",
      "summary", "points", "disagreements", "next_steps",
      "sessions": {slot: {...}},     # 작업장 승계용
      "transcript": {round: {slot: {...}}},
    }
    """
    models = get_effective_models(models)
    if emit is None:
        emit = lambda *_a, **_k: None

    transcript: dict = {}
    sessions: dict = {}
    verdict: dict = {}
    round_no = 0

    for round_no in range(1, max(1, int(max_rounds)) + 1):
        emit("boardroom_round_start", {
            "round": round_no,
            "slots": [s[0] for s in selected_slots],
            "mode": "1차 토론" if round_no == 1 else f"{round_no}차 재질의",
        })

        # 이번 라운드의 슬롯별 프롬프트 (2라운드부터 사회자 재질의 반영)
        prior = transcript.get(round_no - 1, {})
        requeries = (verdict.get("requeries") or {}) if round_no > 1 else {}
        round_sink: dict = {}
        workers = []
        for slot, label, kind, key in selected_slots:
            slot_model = _resolve_slot_model(models, slot, key)
            if round_no == 1:
                prompt, targets = _slot_prompt(agenda, 1, {}), [slot]
            else:
                q = requeries.get(slot)
                if not q:
                    # 재질의 대상이 아니면 이번 라운드는 건너뛴다(불필요한 재발언 방지).
                    continue
                prompt, targets = _slot_prompt(agenda, round_no, prior, q), [slot]

            fn = _run_agent_slot if kind == "agent" else _run_worker_slot
            th = threading.Thread(
                target=fn, args=(emit, slot, label, key, prompt, slot_model, round_sink, round_no),
                daemon=True, name=f"Boardroom-{slot}-R{round_no}",
            )
            th.start()
            workers.append(th)

        for th in workers:
            th.join()

        transcript[round_no] = round_sink
        for slot, row in round_sink.items():
            sessions[slot] = row

        emit("boardroom_round_done", {
            "round": round_no,
            "replies": [round_sink[k] for k in sorted(round_sink)],
        })

        # 재질의 대상이 아무도 없으면(2라운드 이후) 루프 종료
        if round_no > 1 and not round_sink:
            emit("boardroom_round_start", {"round": round_no, "skipped": True})
            break

        verdict = _moderate(agenda, round_no, round_sink, emit=emit)

        if verdict.get("consensus") or round_no >= max_rounds:
            break

        # 다음 라운드에서 물어볼 대상이 없으면 더 돌릴 이유가 없다.
        if not (verdict.get("requeries") or {}):
            break

    conclusion = {
        "summary": verdict.get("summary", ""),
        "points": verdict.get("points", []),
        "disagreements": verdict.get("disagreements", []),
        "next_steps": verdict.get("next_steps", []),
        "consensus": bool(verdict.get("consensus", True)),
        "rounds": round_no,
        "parse_failed": bool(verdict.get("parse_failed")),
    }
    result = {
        "ok": True,
        "agenda": agenda,
        "rounds": round_no,
        "consensus": conclusion["consensus"],
        "summary": conclusion["summary"],
        "points": conclusion["points"],
        "disagreements": conclusion["disagreements"],
        "next_steps": conclusion["next_steps"],
        "sessions": sessions,
        "transcript": transcript,
        "conclusion": conclusion,
    }
    # 회의실 탭(A경로)이든 라온 채팅(B경로)이든 작업장 승계 세션과 결론을 100% 보존
    try:
        remember_panes(result)
    except Exception as _rp_e:
        logger.warning("remember_panes failed in run_boardroom: %s", _rp_e)

    emit("boardroom_conclusion", {
        "rounds": round_no,
        "sessions": sessions,
        **conclusion,
    })
    emit("boardroom_done", {
        "ok": True, "task": agenda, "sessions": sessions,
        "slots": [s[0] for s in selected_slots], "conclusion": conclusion,
    })
    return result


# 참석자 슬롯(02~08). 사회자('01')는 발언하지 않으므로 참여 선택에서 제외한다.
_VALID_SLOT_IDS = [s[0] for s in BOARDROOM_SLOTS]
# 모델은 사회자 슬롯도 보관한다 — 실행에는 쓰이지 않지만 회의실 탭의 8개 드롭다운이
# 서버 왕복 후에도 사용자가 고른 값을 그대로 되돌려 받을 수 있어야 하기 때문.
_MODEL_SLOT_IDS = sorted(set(_VALID_SLOT_IDS + [MODERATOR_SLOT]))
_KNOWN_SLOT_IDS = set(_MODEL_SLOT_IDS)
# 슬롯 번호 ↔ 에이전트/워커 키 별칭. convene_boardroom 도구 스키마가 participants를
# 키로 안내하므로(bill, sherlock, worker-codex ...), 번호와 키를 같은 슬롯으로 해석한다.
# (키만 받던 시절엔 키로 넘어온 participants가 조용히 0명이 되어 회의가 실패했다)
_SLOT_ALIASES = {s[3].lower(): s[0] for s in BOARDROOM_SLOTS}


def _norm_slot_id(value) -> str:
    """'2', 2, ' 02 ' → '02'. 빈 값이면 ''."""
    if value is None:
        return ""
    txt = str(value).strip()
    if not txt:
        return ""
    if txt.isdigit():
        txt = txt.zfill(2)
    return txt


def _resolve_slot_alias(value) -> str:
    """슬롯 번호('02') 또는 에이전트/워커 키('bill') → 슬롯 번호. 모르면 ''."""
    sid = _norm_slot_id(value)
    if not sid:
        return ""
    if sid in _KNOWN_SLOT_IDS:
        return sid
    return _SLOT_ALIASES.get(sid.lower(), "")


def resolve_selected_slots(raw_slots):
    """body.slots / 도구 participants 정규화 → BOARDROOM_SLOTS 부분집합. None이면 전체.

    사회자 슬롯('01')이 넘어와도 참석자로 취급하지 않는다(라온은 발언하지 않음).
    슬롯 번호('02')와 에이전트/워커 키('bill') 둘 다 받는다.
    """
    if raw_slots is None:
        return list(BOARDROOM_SLOTS)
    if isinstance(raw_slots, str):
        raw_slots = [raw_slots]
    if not isinstance(raw_slots, (list, tuple, set)):
        raw_slots = [raw_slots]
    allowed = {_resolve_slot_alias(s) for s in raw_slots}
    allowed.discard("")
    allowed.discard(MODERATOR_SLOT)
    return [s for s in BOARDROOM_SLOTS if s[0] in allowed]


# ── 회의실 선택(참여 슬롯 + 슬롯별 모델) 서버 저장 ────────────────────────────
# 회의실 탭에서 고른 '참여 슬롯'과 '슬롯별 모델'은 원래 브라우저 localStorage에만
# 있었다. 그래서 채팅에서 라온이 convene_boardroom 도구로 회의를 소집하면(B 경로)
# 그 선택을 알 방법이 없어 항상 7명 전원이 참여했다.
# 여기서 마지막 선택을 보관하고, 도구 핸들러가 participants를 명시하지 않았을 때
# 이 값을 읽어 쓴다.
# ★ 메모리만 쓰면 서버 재기동(자기수정 업데이트) 직후 저장값이 사라져, 회의실 탭을
#   다시 열기 전에 채팅으로 소집하면 '전체 참여'로 되돌아간다(버그 재발).
#   그래서 STATE_DIR 아래 JSON 파일에 함께 영속화한다.
_SELECTION_LOCK = threading.Lock()
_SELECTION = {
    "slots": [],
    "models": {},
    "worker_models": {},
    "worker_status": {},
    "saved": False,
}
_SELECTION_LOADED = False  # 디스크 lazy 로드 1회 플래그

# 워커 슬롯(07/08) ↔ Herdr 워커 키. 기본값은 herdr_manager._BOARDROOM_DEFAULTS와
# 같은 값이어야 한다 — 라우트는 여기서 임의의 다른 기본값을 넣지 않는다(§2.1).
_WORKER_SLOTS = ("07", "08")
_WORKER_KEY_BY_SLOT = {"07": "worker-codex", "08": "worker-claude"}
_WORKER_SLOT_BY_KEY = {"worker-codex": "07", "worker-claude": "08"}

# 복원 스레드 1회성 가드 (프로세스당; 저장 변경 시 명시적 재적용은 별도 호출).
_WORKER_RESTORE_LOCK = threading.Lock()
_WORKER_RESTORE_STARTED = False


def _selection_path():
    """회의실 선택 영속 파일 경로.

    STATE_DIR 를 못 찾아도 반드시 유효한 경로를 돌려준다 — None 을 돌려주면
    영속화가 조용히 꺼져 '저장한 줄 알았는데 재기동 후 사라짐'이 재발한다.
    순서: api.config.STATE_DIR → LOCALAPPDATA → 프로젝트 루트/data
    """
    try:
        from api.config import STATE_DIR  # 지연 임포트 — 순환/기동순서 문제 회피
        if STATE_DIR:
            return Path(STATE_DIR) / "boardroom_selection.json"
    except Exception:  # noqa: BLE001
        pass
    try:
        _la = os.getenv("LOCALAPPDATA")
        if _la:
            cand = Path(_la) / "DAON Agent System" / "data"
            cand.mkdir(parents=True, exist_ok=True)
            return cand / "boardroom_selection.json"
    except Exception:  # noqa: BLE001
        pass
    try:
        # api/api/routes/boardroom_routes.py → parents[3] = 프로젝트 루트
        base = Path(__file__).resolve().parents[3] / "data"
        base.mkdir(parents=True, exist_ok=True)
        return base / "boardroom_selection.json"
    except Exception:  # noqa: BLE001
        return None


def _normalize_worker_models_entry(raw_entry) -> dict:
    """worker_models[slot] 1건 정규화 → {provider, model, explicit, updated_at}."""
    entry = raw_entry if isinstance(raw_entry, dict) else {}
    provider = str(entry.get("provider") or "").strip()
    model = str(entry.get("model") or "").strip()
    # 구형식 하위호환: 문자열 단독("deepseek-v4.1-flash") → 명시 모델로 취급.
    if not isinstance(raw_entry, dict) and raw_entry is not None:
        s = str(raw_entry).strip()
        if s:
            model = s
    try:
        explicit = bool(entry.get("explicit", True if model else False))
    except Exception:  # noqa: BLE001
        explicit = bool(model)
    try:
        updated_at = float(entry.get("updated_at") or 0.0)
    except (TypeError, ValueError):  # noqa: BLE001
        updated_at = 0.0
    return {
        "provider": provider,
        "model": model,
        "explicit": bool(explicit),
        "updated_at": updated_at,
    }


def _normalize_worker_models(raw_worker_models) -> dict:
    """저장된 worker_models 전체 정규화. 07/08 외 키는 버린다."""
    if not isinstance(raw_worker_models, dict):
        return {}
    out = {}
    for key, value in raw_worker_models.items():
        slot = _norm_slot_id(key)
        if slot not in _WORKER_SLOTS:
            continue
        out[slot] = _normalize_worker_models_entry(value)
    return out


def _normalize_worker_status(raw_status) -> dict:
    """worker_status 전체 정규화. 07/08 외 키는 버린다."""
    if not isinstance(raw_status, dict):
        return {}
    out = {}
    for key, value in raw_status.items():
        slot = _norm_slot_id(key)
        if slot not in _WORKER_SLOTS:
            continue
        entry = value if isinstance(value, dict) else {}
        try:
            checked_at = float(entry.get("checked_at") or 0.0)
        except (TypeError, ValueError):  # noqa: BLE001
            checked_at = 0.0
        out[slot] = {
            "state": str(entry.get("state") or ""),
            "resolved_model": str(entry.get("resolved_model") or ""),
            "provider": str(entry.get("provider") or ""),
            "error": entry.get("error"),
            "checked_at": checked_at,
        }
    return out


def _synthesize_worker_models(norm_models: dict) -> dict:
    """M-3.1: worker_models가 없으면 models[07/08]로부터 합성 (explicit=true)."""
    out = {}
    now = time.time()
    for slot in _WORKER_SLOTS:
        model = (norm_models or {}).get(slot, "")
        if model:
            out[slot] = {
                "provider": "",
                "model": str(model),
                "explicit": True,
                "updated_at": now,
            }
    return out


def _build_worker_models_update(raw_models, existing: dict):
    """M-3.2/M-3.3: 저장 시 worker_models 갱신.

    - raw_models가 None(키 생략) → 기존 유지(부분 갱신).
    - raw_models가 dict → 07/08 키 존재 여부로 판단. 값이 비어 있으면
      explicit:false(의도된 기본)로 기록하고 키를 살린다(버리지 않음).
      키 자체가 없으면 기존 항목 유지.
    - provider는 가능하면 herdr 해석으로 채우고, 실패하면 "" (복원 시 해석).
    반환: (new_worker_models, changed_slots)
    """
    base = {s: dict(e) for s, e in (existing or {}).items() if s in _WORKER_SLOTS}
    if raw_models is None:
        return base, []
    if not isinstance(raw_models, dict):
        return base, []
    # raw 키 → 슬롯 매핑 (별칭 'worker-codex'도 받는다)
    present = {}
    for key, value in raw_models.items():
        slot = _norm_slot_id(key)
        if not slot and isinstance(key, str):
            slot = _SLOT_ALIASES.get(key.strip().lower(), "")
        if slot not in _WORKER_SLOTS:
            continue
        present[slot] = ("" if value is None else str(value).strip())
    if not present and raw_models:
        # models 키가 전부 다른 슬롯(02~06/01) → 워커 항목은 손대지 않는다.
        return base, []
    changed = []
    now = time.time()
    for slot in _WORKER_SLOTS:
        if slot not in present:
            continue
        model = present[slot]
        old = base.get(slot) or {}
        if model:
            provider = str(old.get("provider") or "")
            if not provider:
                try:
                    from api.managers.herdr_manager import HerdrManager

                    tmp = HerdrManager.__new__(HerdrManager)
                    prov, _m, _e, _n = HerdrManager._boardroom_pair(tmp, _WORKER_KEY_BY_SLOT[slot], model)
                    provider = prov or ""
                    model = _m or model
                except Exception:  # noqa: BLE001
                    provider = provider
            new_entry = {
                "provider": provider,
                "model": model,
                "explicit": True,
                "updated_at": now,
            }
        else:
            new_entry = {
                "provider": str(old.get("provider") or ""),
                "model": "",
                "explicit": False,
                "updated_at": now,
            }
        old_cmp = {k: old.get(k) for k in ("provider", "model", "explicit")}
        new_cmp = {k: new_entry.get(k) for k in ("provider", "model", "explicit")}
        base[slot] = new_entry
        if old_cmp != new_cmp:
            changed.append(slot)
    return base, changed


def _load_selection_disk() -> dict:
    """디스크에서 저장값을 읽는다. 실패하면 빈 선택.

    M-3.1: worker_models가 없으면 models[07/08]로부터 합성한다.
    구 파일(worker_* 키 없음)도 그대로 읽힌다(하위호환).
    """
    empty = {
        "slots": [],
        "models": {},
        "worker_models": {},
        "worker_status": {},
        "saved": False,
    }
    p = _selection_path()
    if not p:
        return empty
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            return empty
        norm_models = normalize_slot_models(raw.get("models"))
        if isinstance(raw.get("worker_models"), dict):
            worker_models = _normalize_worker_models(raw.get("worker_models"))
        else:
            worker_models = _synthesize_worker_models(norm_models)
        return {
            "slots": normalize_slot_ids(raw.get("slots")),
            "models": norm_models,
            "worker_models": worker_models,
            "worker_status": _normalize_worker_status(raw.get("worker_status")),
            "saved": bool(raw.get("saved")),
        }
    except Exception:  # noqa: BLE001 - 파일 없음/손상은 '저장값 없음'으로 처리
        return {"slots": [], "models": {}, "worker_models": {}, "worker_status": {}, "saved": False}


def _save_selection_disk() -> None:
    """현재 메모리 선택을 디스크에 기록한다(실패는 조용히 무시)."""
    p = _selection_path()
    if not p:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with _SELECTION_LOCK:
            payload = {
                "slots": list(_SELECTION["slots"]),
                "models": dict(_SELECTION["models"]),
                "worker_models": {s: dict(e) for s, e in _SELECTION.get("worker_models", {}).items()},
                "worker_status": {s: dict(e) for s, e in _SELECTION.get("worker_status", {}).items()},
                "saved": bool(_SELECTION["saved"]),
            }
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        tmp.replace(p)  # 원자적 교체
    except Exception:  # noqa: BLE001
        pass


def normalize_slot_ids(raw_slots) -> list:
    """참여 슬롯 목록을 BOARDROOM_SLOTS 의 유효 슬롯(02~08)으로 정규화한다.

    - 문자열/숫자 혼용 허용('2', 2, '02' → '02'), 중복 제거, 슬롯 순서로 정렬
    - 사회자('01')와 미지원 슬롯은 버린다
    - None/dict 등 슬롯 목록이 아닌 입력 → 빈 리스트(잘못된 payload를 전체 참여로
      해석하지 않는다). '미지정=전체' 확대는 resolve_selected_slots 담당이므로
      여기서는 확대하지 않는다(저장값 없음과 빈 선택을 호출자가 구분할 수 있게).
    """
    if raw_slots is None:
        return []
    if isinstance(raw_slots, str):
        raw_slots = [raw_slots]
    if not isinstance(raw_slots, (list, tuple, set)):
        raw_slots = [raw_slots]
    allowed = {_norm_slot_id(s) for s in raw_slots}
    allowed.discard("")
    allowed.discard(MODERATOR_SLOT)
    return [sid for sid in _VALID_SLOT_IDS if sid in allowed]


def normalize_slot_models(raw_models) -> dict:
    """슬롯별 모델 선택을 {슬롯번호: 모델id} 로 정규화한다.

    미지원 슬롯 키와 빈 모델값('' = 기본 모델)은 버린다.
    """
    if not isinstance(raw_models, dict):
        return {}
    out = {}
    for key, value in raw_models.items():
        slot = _norm_slot_id(key)
        if slot not in _MODEL_SLOT_IDS:
            continue
        model = "" if value is None else str(value).strip()
        if not model:
            continue
        out[slot] = model
    return {sid: out[sid] for sid in _MODEL_SLOT_IDS if sid in out}


def save_selection(slots, models) -> dict:
    """회의실 탭의 현재 선택을 저장하고 정규화 결과를 돌려준다.

    M-3.2/M-3.3: models 원본(raw)에서 07/08 키 존재를 보고 worker_models를
    함께 갱신한다. models 자체는 기존 동작 유지(빈 값 버림, 구 프론트 호환).
    저장 후 변경된 워커 슬롯은 백그라운드에서 herdr에 즉시 반영한다.
    """
    global _SELECTION_LOADED
    raw_models = models if isinstance(models, dict) else None
    norm_slots = normalize_slot_ids(slots)
    norm_models = normalize_slot_models(models)
    with _SELECTION_LOCK:
        existing_wm = dict(_SELECTION.get("worker_models", {}))
    new_wm, changed = _build_worker_models_update(raw_models, existing_wm)
    with _SELECTION_LOCK:
        _SELECTION.update({
            "slots": norm_slots,
            "models": norm_models,
            "worker_models": new_wm,
            "saved": True,
        })
    _SELECTION_LOADED = True
    _save_selection_disk()
    if changed:
        _apply_worker_models_async("save:" + ",".join(changed))
    return {
        "slots": list(norm_slots),
        "models": dict(norm_models),
        "worker_models": {s: dict(e) for s, e in new_wm.items()},
    }


def get_selection() -> dict:
    """저장된 회의실 선택 원본(정규화된 슬롯 id 목록 + 모델 dict + 워커 상태).

    서버 재기동 직후(_SELECTION 이 비어 있음)에는 디스크에서 1회 복원하고,
    워커 모델 복원 스레드를 1회 기동한다(lazy, fail-open).
    """
    global _SELECTION_LOADED
    if not _SELECTION_LOADED:
        disk = _load_selection_disk()
        with _SELECTION_LOCK:
            # 다른 스레드가 그 사이에 이미 저장했으면 그 값을 우선한다.
            if not _SELECTION["saved"] and disk.get("saved"):
                _SELECTION.update(disk)
        _SELECTION_LOADED = True
    with _SELECTION_LOCK:
        out = {
            "slots": list(_SELECTION["slots"]),
            "models": dict(_SELECTION["models"]),
            "worker_models": {s: dict(e) for s, e in _SELECTION.get("worker_models", {}).items()},
            "worker_status": {s: dict(e) for s, e in _SELECTION.get("worker_status", {}).items()},
            "saved": bool(_SELECTION["saved"]),
        }
    if out["saved"]:
        _apply_worker_models_async("restore")
    return out


def _record_worker_status(slot: str, state: str, resolved_model: str = "",
                           provider: str = "", error=None) -> None:
    """슬롯 적용 결과를 메모리에 기록하고 디스크에 영속화한다(조용한 실패 금지)."""
    with _SELECTION_LOCK:
        status = dict(_SELECTION.get("worker_status", {}))
        status[slot] = {
            "state": state,
            "resolved_model": resolved_model,
            "provider": provider,
            "error": error,
            "checked_at": time.time(),
        }
        _SELECTION["worker_status"] = status
    _save_selection_disk()


def _apply_worker_models_async(reason: str = "") -> None:
    """저장된 워커 모델을 herdr에 반영한다 (백그라운드, 1회성 + 저장변경 시).

    - reason="restore": 프로세스당 1회만 기동 (서버/데몬 재기동 후 복원, I-7).
    - reason="save:..": 저장 변경 시마다 기동 (즉시 반영).
    - herdr 임포트는 함수 내부 지연 임포트 (순환 임포트 회피).
    - 실패는 worker_status에 기록하고 삼키지 않는다. 절대 raise하지 않는다.
    """
    global _WORKER_RESTORE_STARTED
    is_restore = (reason == "restore")
    if is_restore:
        with _WORKER_RESTORE_LOCK:
            if _WORKER_RESTORE_STARTED:
                return
            _WORKER_RESTORE_STARTED = True
    with _SELECTION_LOCK:
        snapshot = {s: dict(e) for s, e in _SELECTION.get("worker_models", {}).items()}
        has_saved = bool(_SELECTION.get("saved"))

    def _bg():
        try:
            from api.managers.herdr_manager import herdr_manager
        except Exception as e:  # noqa: BLE001
            logger.warning("[boardroom] worker restore skipped (herdr import): %s", e)
            for slot in _WORKER_SLOTS:
                if slot in snapshot:
                    _record_worker_status(slot, "failed", "", "", f"herdr_import_failed: {e}")
            return
        for slot in _WORKER_SLOTS:
            entry = snapshot.get(slot) or {}
            worker_key = _WORKER_KEY_BY_SLOT[slot]
            # explicit=false/빈 값 → 빈 문자열로 호출해 herdr 기본값 폴백(라우트가
            # 임의의 기본값을 넣지 않는다 — herdr _BOARDROOM_DEFAULTS가 진실).
            desired = str(entry.get("model") or "") if entry.get("explicit", False) else ""
            if not entry and not has_saved:
                continue  # 저장 자체가 없으면 손대지 않는다
            try:
                st = herdr_manager.set_worker_model(worker_key, desired)
                worker_state = {}
                try:
                    worker_state = herdr_manager.get_worker(worker_key) or {}
                except Exception:  # noqa: BLE001
                    worker_state = {}
                resolved = str(worker_state.get("resolved_model") or st.get("model") or desired)
                provider = str(worker_state.get("provider") or "")
                err = worker_state.get("model_error")
                if err:
                    _record_worker_status(slot, "degraded", resolved, provider, err)
                else:
                    _record_worker_status(slot, "ready", resolved, provider, None)
            except Exception as e:  # noqa: BLE001
                logger.exception("[boardroom] worker apply failed slot=%s", slot)
                _record_worker_status(slot, "failed", desired, "", f"{type(e).__name__}: {e}")

    th = threading.Thread(target=_bg, daemon=True, name=f"BoardroomWorkerApply-{reason}")
    th.start()


def get_saved_slots():
    """저장된 참여 슬롯을 BOARDROOM_SLOTS 부분집합으로 반환한다.

    저장된 적이 없으면 None(호출자가 하위호환 폴백을 결정).
    전원 해제(빈 목록)로 저장된 경우도 빈 리스트를 돌려준다 — 채팅 소집에서
    '아무도 참여 안 함'은 회의를 못 여는 것보다 전체 참여 폴백이 안전하므로
    호출자가 빈 리스트를 '선택 없음'으로 취급한다.
    """
    sel = get_selection()
    if not sel["saved"]:
        return None
    allowed = set(sel["slots"])
    return [s for s in BOARDROOM_SLOTS if s[0] in allowed]


def get_saved_models() -> dict:
    """저장된 슬롯별 모델 오버라이드. 저장값이 없으면 빈 dict."""
    return get_selection()["models"]


def get_effective_models(models: dict = None) -> dict:
    """디스패치용 유효 모델 dict — 호출 인자 + 저장된 워커 명시값을 병합한다.

    07/08에 한해 호출 models에 값이 없고 저장 worker_models가 explicit이면
    저장값을 채운다. explicit=false(의도된 기본)는 비워 둔다 — herdr 기본값
    폴백이 동작한다. 02~06/01은 호출값을 그대로 쓴다.
    """
    merged = dict(models) if isinstance(models, dict) else {}
    try:
        sel = get_selection()
    except Exception:  # noqa: BLE001
        return merged
    stored = sel.get("worker_models") or {}
    for slot in _WORKER_SLOTS:
        if _resolve_slot_model(merged, slot, _WORKER_KEY_BY_SLOT[slot]):
            continue
        entry = stored.get(slot) or {}
        if entry.get("explicit") and str(entry.get("model") or "").strip():
            merged[slot] = str(entry["model"]).strip()
    return merged


# ── HTTP 라우트 ──────────────────────────────────────────────────────────────

# ── 라이브 중계 버퍼 (도구 경로 전용) ────────────────────────────────────────
# convene_boardroom 도구는 라온의 채팅 턴 안에서 돌기 때문에 SSE stream_id가 없다.
# 그래서 회의실 탭이 붙을 수 있도록 전역 링버퍼에 이벤트를 쌓고, 프론트가
# GET /api/boardroom/live?since=N 으로 폴링해 따라간다.
_LIVE_LOCK = threading.Lock()
_LIVE = {"seq": 0, "events": [], "active": False, "agenda": "", "rounds": 0}
_LIVE_MAX_EVENTS = 600


def live_reset(agenda: str = "") -> None:
    with _LIVE_LOCK:
        _LIVE.update({"seq": 0, "events": [], "active": True, "agenda": agenda, "rounds": 0})


def live_emit(event: str, data: dict) -> None:
    """run_boardroom의 emit 콜백 — 전역 버퍼에 이벤트를 쌓는다."""
    with _LIVE_LOCK:
        _LIVE["seq"] += 1
        if event == "boardroom_round_start":
            _LIVE["rounds"] = max(_LIVE.get("rounds", 0), int(data.get("round") or 0))
        if event == "boardroom_done":
            _LIVE["active"] = False
        _LIVE["events"].append({"seq": _LIVE["seq"], "event": event, "data": data})
        if len(_LIVE["events"]) > _LIVE_MAX_EVENTS:
            del _LIVE["events"][:-_LIVE_MAX_EVENTS]


def live_snapshot(since: int = 0) -> dict:
    with _LIVE_LOCK:
        return {
            "ok": True,
            "active": _LIVE["active"],
            "agenda": _LIVE["agenda"],
            "rounds": _LIVE["rounds"],
            "seq": _LIVE["seq"],
            "events": [e for e in _LIVE["events"] if e["seq"] > since],
        }


def _format_report(res: dict) -> str:
    """회의 결론을 채팅 보고용 마크다운으로 만든다(라온이 그대로 인용)."""
    lines = [f"## 회의실 결론 (라운드 {res.get('rounds', 1)}회"
             + (", 합의 도달" if res.get("consensus") else ", 이견 잔존") + ")"]
    if res.get("summary"):
        lines.append(res["summary"])
    if res.get("points"):
        lines.append("\n**합의 사항**")
        lines += [f"- {p}" for p in res["points"]]
    if res.get("disagreements"):
        lines.append("\n**남은 이견**")
        lines += [f"- {d}" for d in res["disagreements"]]
    if res.get("next_steps"):
        lines.append("\n**다음 단계(작업)**")
        lines += [f"- {s}" for s in res["next_steps"]]
    lines.append(
        "\n승인하시면 작업장을 열고 이 작업을 바로 시작합니다."
    )
    return "\n".join(lines)


def handle_get_boardroom_live(handler, parsed) -> bool:
    """GET /api/boardroom/live?since=N — 도구 경로 회의의 실시간 중계 폴링."""
    from urllib.parse import parse_qs
    qs = parse_qs(parsed.query) if parsed.query else {}
    try:
        since = int(qs.get("since", ["0"])[0])
    except (TypeError, ValueError):
        since = 0
    handler.send_json(live_snapshot(since))
    return True


# ── 작업장 승계용 마지막 회의 기억 ────────────────────────────────────────────
# 라온이 회의 결론을 보고하고 대표님 승인을 받은 뒤 open_workshop()을 호출하면,
# 여기 저장된 pane 목록을 그대로 작업장(multi-view)으로 넘긴다.
_LAST_LOCK = threading.Lock()
_LAST_PANES = {"panes": [], "agenda": "", "rounds": 0}


def build_workshop_panes(res: dict) -> list:
    """회의 세션을 작업장 pane 목록으로 변환한다(멀티뷰 localStorage 형식)."""
    panes = []
    sessions = res.get("sessions") or {}
    for slot in sorted(sessions):
        row = sessions.get(slot) or {}
        if not row.get("ok"):
            continue
        panes.append({
            "profile": row.get("profile") or row.get("key") or "",
            "sessionId": row.get("session_id") or "",
            "model": row.get("model") or "",
            "slot": slot,
            "speaker": row.get("speaker") or "",
            "kind": row.get("kind") or "agent",
        })
    # 라온(pane 리더)을 맨 앞에 — 멀티뷰가 라온 지휘로 시작한다.
    panes.sort(key=lambda p: (0 if p.get("profile") == "raon" else 1, p.get("slot") or ""))
    return panes


def remember_panes(res: dict) -> list:
    panes = build_workshop_panes(res)
    conc = res.get("conclusion") or {}
    with _LAST_LOCK:
        _LAST_PANES.update({
            "panes": panes,
            "agenda": res.get("agenda", ""),
            "rounds": res.get("rounds", 0),
            "summary": conc.get("summary") or res.get("summary", ""),
            "points": conc.get("points") or res.get("points", []),
            "disagreements": conc.get("disagreements") or res.get("disagreements", []),
            "next_steps": conc.get("next_steps") or res.get("next_steps", []),
            "consensus": conc.get("consensus", True) if "consensus" in conc else res.get("consensus", True),
            "report": _format_report(res) if "_format_report" in globals() else "",
            "timestamp": time.time(),
        })
    return panes


def get_last_panes() -> dict:
    with _LAST_LOCK:
        return {
            "panes": list(_LAST_PANES["panes"]),
            "agenda": _LAST_PANES.get("agenda", ""),
            "rounds": _LAST_PANES.get("rounds", 0),
            "summary": _LAST_PANES.get("summary", ""),
            "points": list(_LAST_PANES.get("points", [])),
            "disagreements": list(_LAST_PANES.get("disagreements", [])),
            "next_steps": list(_LAST_PANES.get("next_steps", [])),
            "consensus": bool(_LAST_PANES.get("consensus", True)),
            "report": _LAST_PANES.get("report", ""),
            "timestamp": _LAST_PANES.get("timestamp", 0.0),
        }


def handle_get_boardroom_last(handler, parsed) -> bool:
    """GET /api/boardroom/last — 마지막 회의 결론 및 작업장 세션 목록을 조회한다."""
    info = get_last_panes()
    handler.send_json({
        "ok": True,
        **info,
    })
    return True


def handle_get_boardroom_selection(handler, parsed) -> bool:
    """GET /api/boardroom/selection — 저장된 회의실 참여 선택을 조회한다.

    프론트가 페이지 로드 시 이 값을 우선 적용하고(localStorage는 폴백),
    채팅 소집 경로(_boardroom_handler)도 같은 값을 읽는다.
    worker_models/worker_status도 함께 내려 프론트 뱃지를 갱신한다.
    """
    sel = get_selection()
    handler.send_json({
        "ok": True,
        "saved": sel["saved"],
        "slots": sel["slots"],
        "models": sel["models"],
        "worker_models": sel.get("worker_models", {}),
        "worker_status": sel.get("worker_status", {}),
        "all_slots": list(_VALID_SLOT_IDS),
        "moderator": MODERATOR_SLOT,
    })
    return True


def handle_post_boardroom_selection(handler, body: dict) -> bool:
    """POST /api/boardroom/selection — 회의실 참여 선택을 저장한다.

    body: {"slots": ["02","07"], "models": {"02": "glm-5.3-flash"}}
    키를 아예 생략하면 그 항목은 기존 저장값을 유지한다(부분 갱신 허용).
    models 원본(raw)을 save_selection에 넘겨 07/08의 빈 값(의도된 기본)과
    키 생략(유지)을 구분한다(M-3.3).
    """
    body = body if isinstance(body, dict) else {}
    cur = get_selection()
    slots = normalize_slot_ids(body.get("slots")) if "slots" in body else cur["slots"]
    raw_models = body.get("models") if "models" in body else cur["models"]
    saved = save_selection(slots, raw_models)
    logger.info("[boardroom] selection saved slots=%s models=%s worker_models=%s",
                saved["slots"], saved["models"], saved.get("worker_models", {}))
    handler.send_json({
        "ok": True,
        "saved": True,
        "slots": saved["slots"],
        "models": saved["models"],
        "worker_models": saved.get("worker_models", {}),
        "worker_status": get_selection().get("worker_status", {}),
        "all_slots": list(_VALID_SLOT_IDS),
        "moderator": MODERATOR_SLOT,
    })
    return True


def handle_post_boardroom_broadcast(handler, body: dict) -> bool:
    """POST /api/boardroom/broadcast — 회의실 탭에서 안건을 소집한다."""
    agenda = (body.get("task") or body.get("topic") or "").strip()
    if not agenda:
        handler.send_json({"ok": False, "error": "task is required"}, 400)
        return True

    models = body.get("models") or {}
    if not isinstance(models, dict):
        models = {}

    selected_slots = resolve_selected_slots(body.get("slots"))
    if not selected_slots:
        handler.send_json(
            {"ok": False, "error": "참여할 에이전트를 1명 이상 선택하세요. (no slots selected)"}, 400
        )
        return True

    # 회의실 탭에서 실제로 소집한 선택을 기억해 둔다 — 이후 채팅 소집
    # (convene_boardroom)도 같은 참여/모델 구성을 쓰도록.
    # models 를 안 보낸 호출자는 기존 저장 모델을 유지한다(부분 갱신).
    if body.get("slots") is not None:
        save_selection([s[0] for s in selected_slots],
                       models if body.get("models") is not None else get_saved_models())

    try:
        max_rounds = int(body.get("max_rounds") or MAX_ROUNDS)
    except (TypeError, ValueError):
        max_rounds = MAX_ROUNDS
    max_rounds = max(1, min(max_rounds, MAX_ROUNDS))

    try:
        from api.config import STREAMS, STREAMS_LOCK
        from api.streaming import BroadcastQueue
    except Exception as e:  # noqa: BLE001
        handler.send_json({"ok": False, "error": f"stream infra unavailable: {e}"}, 500)
        return True

    stream_id = uuid.uuid4().hex
    q = BroadcastQueue()
    with STREAMS_LOCK:
        STREAMS[stream_id] = q

    def emit(event: str, data: dict) -> None:
        try:
            q.put((event, data))
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(
        target=run_boardroom,
        kwargs={"agenda": agenda, "selected_slots": selected_slots,
                "models": models, "emit": emit, "max_rounds": max_rounds},
        daemon=True, name="BoardroomBroadcast",
    ).start()

    handler.send_json({
        "ok": True, "stream_id": stream_id,
        "slots": len(selected_slots), "slot_ids": [s[0] for s in selected_slots],
        "moderator": MODERATOR_SLOT, "max_rounds": max_rounds,
    })
    return True
