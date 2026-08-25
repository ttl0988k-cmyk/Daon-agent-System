"""
Daon Agent System — Expert Debate & Multi-Agent Meeting Mode routes.
Hermes MoA-style parallel fan-out, robust timeout/locking, and Moderator-orchestrated meeting loop.
"""
import logging
import threading
import time
import queue
import uuid
import json
import traceback
from pathlib import Path
import concurrent.futures

from api.helpers import j, bad
from api.models import get_session
from api.config import STREAMS, STREAMS_LOCK, _get_session_agent_lock

_logger = logging.getLogger(__name__)

# Global active debates/meetings state: session_id -> debate state dictionary
_active_debates = {}
_active_debates_lock = threading.Lock()

# LLM execution timeout per call (seconds)
LLM_TIMEOUT_SECONDS = 120
SESSION_LOCK_TIMEOUT_SECONDS = 25


def _get_model_label(model_id):
    if not model_id:
        return "Unknown"
    friendly = {
        'anthropic/claude-sonnet-4': 'Claude Sonnet 4',
        'anthropic/claude-opus-4': 'Claude Opus 4',
        'anthropic/claude-haiku-4': 'Claude Haiku 4',
        'anthropic/claude-3.5-sonnet': 'Claude 3.5 Sonnet',
        'openai/gpt-4o': 'GPT-4o',
        'openai/gpt-4o-mini': 'GPT-4o Mini',
        'openai/gpt-4-turbo': 'GPT-4 Turbo',
        'gemini-2.5-pro': 'Gemini 2.5 Pro',
        'gemini-2.5-flash': 'Gemini 2.5 Flash',
        'gemini-1.5-pro': 'Gemini 1.5 Pro',
        'deepseek-v4-pro': 'DeepSeek V4 Pro',
        'deepseek-v3': 'DeepSeek V3',
    }
    if model_id in friendly:
        return friendly[model_id]
    return str(model_id).split('/')[-1].replace('-', ' ').replace('_', ' ').title()


def _raw_call_llm(model_id, system_prompt, user_prompt) -> str:
    """Synchronous direct LLM call without streaming."""
    from api.config import resolve_model_provider
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from agent.auxiliary_client import call_llm

    _model, _provider, _base_url = resolve_model_provider(model_id)
    # [2026-08-26 토론 401 수정] 설정창 동적 프로바이더(custom_providers.json) 키 최우선.
    # resolve_runtime_provider는 ~/.hermes 인증 체인만 보므로 구식 .env 키로 떨어져 401 유발.
    _dyn_key = ""
    if _provider and not str(_provider).startswith("custom"):
        try:
            from api.managers.model_manager import model_manager as _mm
            _dyn_key = _mm._get_api_key(_provider) or ""
        except Exception as _key_err:
            _logger.warning("Debate dynamic-provider key lookup failed for %s: %s", _provider, _key_err)
    _rt = resolve_runtime_provider(requested=_provider)
    _api_key = _dyn_key or _rt.get("api_key")
    rt_provider = _rt.get("provider")
    rt_base_url = _rt.get("base_url")
    if not _provider or str(_provider).startswith('custom:'):
        _provider = rt_provider
    if not _base_url or str(_provider).startswith('custom'):
        _base_url = rt_base_url
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    if _provider == 'minimax':
        call_provider = 'custom'
        _base_url = 'https://api.minimax.io/v1'
    else:
        call_provider = _provider

    resp = call_llm(
        provider=call_provider,
        model=_model,
        base_url=_base_url,
        api_key=_api_key,
        messages=messages
    )
    return resp.choices[0].message.content or ""


def _execute_debate_llm(model_id, system_prompt, user_prompt, stream_fn=None, timeout_sec=LLM_TIMEOUT_SECONDS, is_cancelled_fn=None) -> str:
    """
    Executes LLM call with a strict timeout and graceful fallback.
    Streams output chunks via stream_fn if provided.
    """
    model_label = _get_model_label(model_id)
    if is_cancelled_fn and is_cancelled_fn():
        return "[작업 취소됨]"

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_raw_call_llm, model_id, system_prompt, user_prompt)

    try:
        content = future.result(timeout=timeout_sec)
    except concurrent.futures.TimeoutError:
        _logger.warning("Debate LLM call for %s timed out after %ds", model_id, timeout_sec)
        msg = f"⚠️ [{model_label}] 응답 시간 초과 ({timeout_sec}초 제한 도달 — 건너뜀)"
        if stream_fn:
            stream_fn(msg)
        return "__DEBATE_FAILED__::timeout::" + msg
    except Exception as e:
        _logger.error("Debate LLM call for %s failed: %s", model_id, e, exc_info=True)
        err_text = str(e)
        reason = "unknown"
        for code in ("401", "403", "404", "429", "500", "502", "503"):
            if code in err_text:
                reason = code
                break
        msg = f"⚠️ [{model_label}] 응답 생성 실패 ({reason}): {err_text[:200]}"
        if stream_fn:
            stream_fn(msg)
        return f"__DEBATE_FAILED__::{reason}::{msg}"
    finally:
        executor.shutdown(wait=False)

    if is_cancelled_fn and is_cancelled_fn():
        return "[작업 취소됨]"

    # Smooth streaming chunking
    if stream_fn and content:
        chunk_size = 6
        delay = 0.012
        for i in range(0, len(content), chunk_size):
            if is_cancelled_fn and is_cancelled_fn():
                break
            chunk = content[i:i+chunk_size]
            stream_fn(chunk)
            time.sleep(delay)

    return content


def _run_debate_round_thread(session_id):
    """Worker thread running either 3-Round Debate Mode or Multi-Agent Meeting Mode."""
    with _active_debates_lock:
        state = _active_debates.get(session_id)
    if not state:
        return

    stream_id = state["stream_id"]
    q = STREAMS.get(stream_id)
    if not q:
        return

    def put(event, data):
        try:
            q.put_nowait((event, data))
        except Exception:
            pass

    def is_cancelled():
        return bool(state.get("cancelled", False))

    # Heartbeat background thread to keep SSE connection alive
    hb_stop_event = threading.Event()
    def _heartbeat_worker():
        while not hb_stop_event.wait(15.0):
            put('heartbeat', {'ts': int(time.time())})

    hb_thread = threading.Thread(target=_heartbeat_worker, daemon=True)
    hb_thread.start()

    session_lock = _get_session_agent_lock(session_id)
    lock_acquired = session_lock.acquire(timeout=SESSION_LOCK_TIMEOUT_SECONDS)
    if not lock_acquired:
        _logger.warning("Failed to acquire session lock for %s in debate thread", session_id)
        put('error', {'message': '세션 락을 획득하지 못했습니다. 다른 작업이 완료된 후 다시 시도해 주세요.'})
        hb_stop_event.set()
        return

    try:
        s = get_session(session_id)
        if not s:
            put('error', {'message': f'세션 ({session_id})을 찾을 수 없습니다.'})
            return

        mode = state.get("mode", "debate")

        # =========================================================================
        # ⚖️ MODE 1: 3-ROUND DEBATE (병렬 MoA 방식)
        # =========================================================================
        if mode == "debate":
            # --- Round 1: 각 모델의 주장 수집 (병렬 Fan-Out) ---
            if state["current_round"] == 1:
                put('debate_status', {'text': '1라운드: 각 모델의 주장 병렬 수집 중...', 'round': 1})
                models = state["models"]

                def _worker_round1(m_id):
                    if is_cancelled():
                        return m_id, "[작업 취소됨]"
                    m_lbl = _get_model_label(m_id)
                    sender_tag = f"🤖 {m_lbl} (주장)"
                    res = _execute_debate_llm(
                        model_id=m_id,
                        system_prompt=(
                            "당신은 전문가 토론의 참여자입니다. 주어진 주제에 대해 본인의 고유한 분석과 주장을 마크다운(Markdown) 포맷으로 작성해 주세요.\n"
                            "중복된 코드 블록으로 전체 글을 감싸지 말고 일반 마크다운 글로 작성해 주세요. 반드시 한국어로 작성해야 합니다."
                        ),
                        user_prompt=f"토론 주제: {state['topic']}",
                        stream_fn=lambda token: put('debate_token', {'sender': sender_tag, 'model_id': m_id, 'text': token}),
                        is_cancelled_fn=is_cancelled
                    )
                    put('debate_message_done', {'sender': sender_tag, 'model_id': m_id})
                    return m_id, res

                failed_models = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(models)) as pool:
                    futures = [pool.submit(_worker_round1, m) for m in models]
                    for fut in concurrent.futures.as_completed(futures):
                        try:
                            m_id, content = fut.result()
                            if isinstance(content, str) and content.startswith("__DEBATE_FAILED__"):
                                parts = content.split("::", 2)
                                reason = parts[1] if len(parts) > 1 else "unknown"
                                failed_models.append((m_id, reason))
                                state["round1_responses"][m_id] = (content, reason)
                            else:
                                state["round1_responses"][m_id] = (content, None)
                            m_lbl = _get_model_label(m_id)
                            msg = {
                                'role': 'assistant',
                                'content': content,
                                'sender': f"🤖 {m_lbl} (주장)",
                                'is_debate': True,
                                'timestamp': int(time.time())
                            }
                            s.messages.append(msg)
                            s.save()
                        except Exception as worker_e:
                            _logger.error("Round 1 worker exception: %s", worker_e)
                            failed_models.append((m_id, "exception"))

                put('debate_health', {'models': [
                    {'model_id': m_id, 'label': _get_model_label(m_id),
                     'status': 'failed' if any(f[0] == m_id for f in failed_models) else 'ok',
                     'reason': next((r for (mid, r) in failed_models if mid == m_id), None)}
                    for m_id in models
                ]})
                for (m_id, reason) in failed_models:
                    put('debate_partial_failed', {
                        'model_id': m_id,
                        'sender': f"🤖 {_get_model_label(m_id)} (주장)",
                        'reason': reason,
                        'round': 1,
                    })

                if not is_cancelled():
                    if failed_models and len(failed_models) == len(models):
                        put('debate_status', {'text': f'❌ 모든 모델({len(failed_models)}개) 응답 실패. 키/설정 확인 후 다시 시작하세요.', 'round': 1, 'waiting_next': False})
                    else:
                        state["current_round"] = 2
                        put('debate_status', {'text': f'1라운드 완료 ({len(failed_models)}개 실패). 다음 버튼을 눌러주세요.', 'waiting_next': True, 'round': 1})
                    put('done', {'session': s.compact() | {'messages': s.messages}})

            # --- Round 2: 상호 반박 수집 (병렬 Fan-Out) ---
            elif state["current_round"] == 2:
                put('debate_status', {'text': '2라운드: 상호 반박 병렬 수집 중...', 'round': 2})
                models = state["models"]

                def _worker_round2(m_id):
                    if is_cancelled():
                        return m_id, "[작업 취소됨]"
                    m_lbl = _get_model_label(m_id)
                    sender_tag = f"💬 {m_lbl} (반박)"
                    others_text = ""
                    for other_m_id, resp in state["round1_responses"].items():
                        if other_m_id != m_id:
                            others_text += f"=== {_get_model_label(other_m_id)}의 주장 ===\n{resp}\n\n"

                    res = _execute_debate_llm(
                        model_id=m_id,
                        system_prompt=(
                            "당신은 전문가 토론의 참여자입니다. 다른 참여자들의 1라운드 주장을 분석하여 "
                            "상대방 주장의 쟁점이나 한계를 지적하고 본인의 논리를 방어하는 반박문을 작성하세요.\n"
                            "반드시 한국어로 작성하고, 마크다운(Markdown) 형식으로 깔끔하게 작성해 주세요."
                        ),
                        user_prompt=(
                            f"토론 주제: {state['topic']}\n\n"
                            f"아래는 다른 모델들의 1라운드 주장입니다:\n\n{others_text}\n"
                            "상대방 주장의 단점을 지적하며, 본인의 제안을 옹호하는 반박문을 작성해 주세요."
                        ),
                        stream_fn=lambda token: put('debate_token', {'sender': sender_tag, 'model_id': m_id, 'text': token}),
                        is_cancelled_fn=is_cancelled
                    )
                    put('debate_message_done', {'sender': sender_tag, 'model_id': m_id})
                    return m_id, res

                failed_r2 = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(models)) as pool:
                    futures = [pool.submit(_worker_round2, m) for m in models]
                    for fut in concurrent.futures.as_completed(futures):
                        try:
                            m_id, content = fut.result()
                            if isinstance(content, str) and content.startswith("__DEBATE_FAILED__"):
                                parts = content.split("::", 2)
                                reason = parts[1] if len(parts) > 1 else "unknown"
                                failed_r2.append((m_id, reason))
                                state["round2_responses"][m_id] = (content, reason)
                                put('debate_partial_failed', {
                                    'model_id': m_id,
                                    'sender': f"💬 {_get_model_label(m_id)} (반박)",
                                    'reason': reason,
                                    'round': 2,
                                })
                            else:
                                state["round2_responses"][m_id] = (content, None)
                            m_lbl = _get_model_label(m_id)
                            msg = {
                                'role': 'assistant',
                                'content': content,
                                'sender': f"💬 {m_lbl} (반박)",
                                'is_debate': True,
                                'timestamp': int(time.time())
                            }
                            s.messages.append(msg)
                            s.save()
                        except Exception as worker_e:
                            _logger.error("Round 2 worker exception: %s", worker_e)
                            failed_r2.append((m_id, "exception"))

                if not is_cancelled():
                    if failed_r2 and len(failed_r2) == len(models):
                        put('debate_status', {'text': f'❌ 모든 모델({len(failed_r2)}개) 응답 실패. 라운드 2 중단.', 'round': 2, 'waiting_next': False})
                    else:
                        state["current_round"] = 3
                        put('debate_status', {'text': f'2라운드 완료 ({len(failed_r2)}개 실패). 최종 판결 버튼을 눌러주세요.', 'waiting_next': True, 'round': 2})
                    put('done', {'session': s.compact() | {'messages': s.messages}})

            # --- Round 3: 최종 판결문 및 계획안 생성 ---
            elif state["current_round"] == 3:
                put('debate_status', {'text': '최종 판결: 판결문 및 추천 실행 계획안 생성 중...', 'round': 3})
                judge_model_id = state.get("judge_model") or s.model
                judge_label = _get_model_label(judge_model_id)

                transcript = ""
                for m_id in state["models"]:
                    m_lbl = _get_model_label(m_id)
                    r1_entry = state["round1_responses"].get(m_id, ("", None))
                    r2_entry = state["round2_responses"].get(m_id, ("", None))
                    r1, r1_fail = (r1_entry if isinstance(r1_entry, tuple) else (r1_entry, None))
                    r2, r2_fail = (r2_entry if isinstance(r2_entry, tuple) else (r2_entry, None))
                    if r1_fail:
                        transcript += f"■ {m_lbl} (1라운드 주장): [응답 실패 (HTTP {r1_fail}) — 이 모델의 주장은 사용 불가]\n\n"
                    else:
                        transcript += f"■ {m_lbl} (1라운드 주장):\n{r1}\n\n"
                    if r2_fail:
                        transcript += f"■ {m_lbl} (2라운드 반박): [응답 실패 (HTTP {r2_fail}) — 이 모델의 반박은 사용 불가]\n\n"
                    else:
                        transcript += f"■ {m_lbl} (2라운드 반박):\n{r2}\n\n"

                content = _execute_debate_llm(
                    model_id=judge_model_id,
                    system_prompt=(
                        "당신은 공정하고 통찰력 있는 판사 AI 에이전트입니다. "
                        "사용자가 던진 주제와 여러 AI 모델들의 토론 내용(주장 및 반박)을 종합적으로 평가하여 최종 판결을 내리세요.\n"
                        "반드시 한국어로 작성하며, 다음 목차를 포함하여 마크다운(Markdown) 포맷으로 구체적으로 출력해 주세요:\n"
                        "1. ⚖️ 토론 핵심 쟁점 요약\n"
                        "2. 📋 추천 구현 계획안 (Harness/CLI에 복사해서 사용 가능하게 구조화)\n"
                        "3. 🤖 일반 에이전트용 마스터 프롬프트 (사용자가 복사해서 일반 대화창에 넣고 수행할 수 있는 프롬프트 템플릿)"
                    ),
                    user_prompt=(
                        f"토론 주제: {state['topic']}\n\n"
                        f"=== [토론 내역] ===\n\n{transcript}\n"
                        "위 토론 내역을 객관적으로 분석하여 최종 판결문을 마크다운 형식으로 작성해 주세요."
                    ),
                    stream_fn=lambda token: put('debate_token', {
                        'sender': f"⚖️ 판사 ({judge_label})",
                        'model_id': judge_model_id,
                        'text': token
                    }),
                    is_cancelled_fn=is_cancelled
                )

                state["judge_response"] = content
                msg = {
                    'role': 'assistant',
                    'content': content,
                    'sender': f"⚖️ 판사 ({judge_label})",
                    'is_debate': True,
                    'timestamp': int(time.time())
                }
                s.messages.append(msg)
                s.save()

                put('debate_message_done', {'sender': f"⚖️ 판사 ({judge_label})", 'model_id': judge_model_id})
                state["current_round"] = 4
                put('debate_status', {'text': '토론 및 최종 판결 완료.', 'completed': True, 'round': 3})
                put('done', {'session': s.compact() | {'messages': s.messages}})

        # =========================================================================
        # 👥 MODE 2: MULTI-AGENT MEETING (사회자 오케스트레이션 및 턴 예산제)
        # =========================================================================
        elif mode == "meeting":
            max_turns = int(state.get("max_turns", 8))
            current_turn = int(state.get("current_turn", 0))
            moderator_model = state.get("moderator_model") or s.model
            moderator_label = _get_model_label(moderator_model)
            participants = state.get("models", [])

            # While turns remain and not concluded
            should_conclude = (current_turn >= max_turns)

            if not should_conclude and not is_cancelled():
                current_turn += 1
                state["current_turn"] = current_turn

                # 1. Moderator step: decide next speaker and specific question
                put('debate_status', {
                    'text': f'사회자 ({moderator_label})가 회의 흐름을 분석하고 발언자를 지목 중입니다... [턴 {current_turn}/{max_turns}]',
                    'turn': current_turn,
                    'max_turns': max_turns
                })

                history_text = ""
                for entry in state.get("history", []):
                    history_text += f"[{entry['speaker']}]: {entry['content']}\n\n"

                participants_info = "\n".join([f"- ID: {m} (이름: {_get_model_label(m)})" for m in participants])

                moderator_system = (
                    "당신은 고도로 유능한 AI 다자간 회의의 의장/사회자(Moderator)입니다.\n"
                    "참여 에이전트 목록:\n"
                    f"{participants_info}\n\n"
                    "역할 규칙:\n"
                    "1. 전체 회의 기록과 주제를 검토하여, 논의 발전에 가장 필요한 다음 발언자를 1명 선택하고, 그 에이전트에게 던질 구체적인 질문 또는 지시를 작성하세요.\n"
                    "2. 특정 에이전트만 계속 지목하지 말고 여러 참여자가 골고루 의견을 낼 수 있도록 안배하세요.\n"
                    "3. 논의가 충분히 무르익어 합의가 이루어졌거나 더 이상 추가 의견이 필요 없다고 판단되면 action을 'conclude'로 설정하세요.\n"
                    "4. 반드시 유효한 JSON 형식으로만 응답하세요. 다른 설명 문구를 붙이지 마세요.\n"
                    '형식: {"action": "speak" | "conclude", "next_speaker": "model_id", "question": "질문/지시사항", "reason": "지목 사유"}'
                )

                moderator_prompt = (
                    f"회의 주제: {state['topic']}\n"
                    f"현재 진행 턴: {current_turn} / 최대 턴: {max_turns}\n\n"
                    f"=== [지금까지의 회의록] ===\n{history_text or '(아직 이전 발언 없음)'}\n\n"
                    "다음 발언자와 질문을 결정하여 JSON으로 응답해 주세요."
                )

                mod_raw = _execute_debate_llm(
                    model_id=moderator_model,
                    system_prompt=moderator_system,
                    user_prompt=moderator_prompt,
                    stream_fn=None,
                    is_cancelled_fn=is_cancelled
                )

                action = "speak"
                next_speaker = participants[0] if participants else moderator_model
                question = "해당 주제에 대한 귀하의 전문적인 견해와 제안을 말씀해 주세요."
                reason = "회의 시작 발언"

                try:
                    # Clean markdown codeblocks if returned
                    cleaned = mod_raw.strip()
                    if cleaned.startswith("```"):
                        cleaned = cleaned.split("\n", 1)[1]
                        if cleaned.endswith("```"):
                            cleaned = cleaned.rsplit("```", 1)[0]
                    parsed_mod = json.loads(cleaned.strip())
                    action = parsed_mod.get("action", "speak")
                    if parsed_mod.get("next_speaker") in participants:
                        next_speaker = parsed_mod.get("next_speaker")
                    elif participants:
                        # Fallback to round-robin
                        next_speaker = participants[(current_turn - 1) % len(participants)]
                    question = parsed_mod.get("question", question)
                    reason = parsed_mod.get("reason", reason)
                except Exception as parse_e:
                    _logger.warning("Failed to parse moderator JSON (%s), using fallback", parse_e)
                    if participants:
                        next_speaker = participants[(current_turn - 1) % len(participants)]

                if action == "conclude" or current_turn >= max_turns:
                    should_conclude = True
                else:
                    speaker_label = _get_model_label(next_speaker)
                    # Emit moderator pick event
                    put('moderator_pick', {
                        'speaker': speaker_label,
                        'speaker_id': next_speaker,
                        'question': question,
                        'reason': reason,
                        'turn': current_turn,
                        'max_turns': max_turns
                    })

                    # 2. Participant speaks
                    put('debate_status', {
                        'text': f'[{current_turn}/{max_turns}턴] {speaker_label} 발언 생성 중...',
                        'turn': current_turn,
                        'max_turns': max_turns
                    })

                    sender_tag = f"💬 {speaker_label} (발언 #{current_turn})"
                    participant_system = (
                        f"당신은 AI 다자간 회의의 전문 패널 '{speaker_label}'입니다.\n"
                        "사회자의 질문과 지금까지의 회의 맥락을 고려하여, 본인의 전문성에 기반한 명확하고 건설적인 의견을 제시하세요.\n"
                        "다른 참가자의 의견에 대해 논리적으로 동의하거나 보완/반론을 제기할 수 있습니다.\n"
                        "반드시 한국어로 작성하고 마크다운(Markdown) 포맷을 사용해 주세요."
                    )
                    participant_prompt = (
                        f"회의 주제: {state['topic']}\n\n"
                        f"=== [회의 진행 기록] ===\n{history_text or '(첫 발언입니다)'}\n\n"
                        f"🎙️ [사회자의 질문/요청]\n{question}\n\n"
                        "위 질문에 대한 답변과 본인의 의견을 마크다운으로 작성해 주세요."
                    )

                    speaker_content = _execute_debate_llm(
                        model_id=next_speaker,
                        system_prompt=participant_system,
                        user_prompt=participant_prompt,
                        stream_fn=lambda token: put('debate_token', {'sender': sender_tag, 'model_id': next_speaker, 'text': token}),
                        is_cancelled_fn=is_cancelled
                    )

                    state["history"].append({
                        'turn': current_turn,
                        'speaker': speaker_label,
                        'speaker_id': next_speaker,
                        'question': question,
                        'content': speaker_content
                    })

                    msg = {
                        'role': 'assistant',
                        'content': f"> **🎙️ 사회자 질문**: {question}\n\n{speaker_content}",
                        'sender': sender_tag,
                        'is_debate': True,
                        'timestamp': int(time.time())
                    }
                    s.messages.append(msg)
                    s.save()

                    put('debate_message_done', {'sender': sender_tag, 'model_id': next_speaker})

                    if current_turn >= max_turns:
                        should_conclude = True
                    else:
                        put('debate_status', {
                            'text': f'[{current_turn}/{max_turns}턴 완료] 다음 발언 진행 버튼을 누르거나 계속 진행하세요.',
                            'waiting_next': True,
                            'turn': current_turn,
                            'max_turns': max_turns
                        })
                        put('done', {'session': s.compact() | {'messages': s.messages}})

            # 3. Conclusion & Judge Verdict
            if should_conclude and not is_cancelled():
                put('debate_status', {'text': '회의 종료: 최종 판결 및 추천 계획안 생성 중...', 'turn': current_turn, 'max_turns': max_turns})
                judge_model_id = state.get("judge_model") or s.model
                judge_label = _get_model_label(judge_model_id)

                full_meeting_transcript = ""
                for item in state.get("history", []):
                    full_meeting_transcript += f"■ [{item['turn']}턴] {item['speaker']}\n- 사회자 질문: {item['question']}\n- 발언 내용: {item['content']}\n\n"

                content = _execute_debate_llm(
                    model_id=judge_model_id,
                    system_prompt=(
                        "당신은 공정하고 통찰력 있는 판사 AI 에이전트입니다. "
                        "다자간 AI 회의록을 종합 분석하여 핵심 합의점과 최종 권고안을 판결문 형태로 작성하세요.\n"
                        "반드시 한국어로 작성하며, 다음 목차를 포함하여 마크다운(Markdown) 포맷으로 구체적으로 출력해 주세요:\n"
                        "1. ⚖️ 회의 핵심 결론 및 합의점 요약\n"
                        "2. 📋 추천 실행/구현 계획안 (Harness/CLI에서 바로 복사하여 실행 가능한 구조화된 액션 플랜)\n"
                        "3. 🤖 일반 에이전트용 마스터 프롬프트 (사용자가 일반 채팅창에 넣고 실행할 수 있는 지시문)"
                    ),
                    user_prompt=(
                        f"회의 주제: {state['topic']}\n\n"
                        f"=== [전체 회의록] ===\n\n{full_meeting_transcript}\n"
                        "위 회의록을 객관적이고 종합적으로 분석하여 최종 판결문을 작성해 주세요."
                    ),
                    stream_fn=lambda token: put('debate_token', {
                        'sender': f"⚖️ 판사 ({judge_label})",
                        'model_id': judge_model_id,
                        'text': token
                    }),
                    is_cancelled_fn=is_cancelled
                )

                state["judge_response"] = content
                msg = {
                    'role': 'assistant',
                    'content': content,
                    'sender': f"⚖️ 판사 ({judge_label})",
                    'is_debate': True,
                    'timestamp': int(time.time())
                }
                s.messages.append(msg)
                s.save()

                put('debate_message_done', {'sender': f"⚖️ 판사 ({judge_label})", 'model_id': judge_model_id})
                state["completed"] = True
                put('debate_status', {'text': '회의 및 최종 판결 완료.', 'completed': True, 'turn': current_turn, 'max_turns': max_turns})
                put('done', {'session': s.compact() | {'messages': s.messages}})

    except Exception as e:
        _logger.error("Debate thread encountered exception: %s", e, exc_info=True)
        traceback.print_exc()
        put('error', {'message': f'토론/회의 진행 중 오류 발생: {str(e)}'})
    finally:
        hb_stop_event.set()
        if lock_acquired:
            try:
                session_lock.release()
            except Exception:
                pass


# ── POST route helpers ────────────────────────────────────────────────────────

def handle_post_debate_start(handler, body) -> bool:
    """POST /api/debate/start — 토론 또는 회의 시작"""
    try:
        session_id = body.get('session_id')
        topic = body.get('topic', '').strip()
        models = body.get('models', [])
        mode = body.get('mode', 'debate')  # 'debate' or 'meeting'
        max_turns = int(body.get('max_turns', 8))
        moderator_model = body.get('moderator_model')
        judge_model = body.get('judge_model')

        if not session_id or not topic or not models:
            return bad(handler, 'session_id, topic, and models are required')

        if len(models) < 2:
            return bad(handler, '최소 2개 이상의 모델을 선택해야 합니다.')

        s = get_session(session_id)
        if not s:
            return bad(handler, 'Session not found', 404)

        if not moderator_model:
            moderator_model = s.model or models[0]
        if not judge_model:
            judge_model = s.model or models[0]

        mode_name = "👥 다자간 회의" if mode == "meeting" else "⚖️ 전문가 토론"
        model_names = ', '.join([_get_model_label(m) for m in models])

        # Append initial user topic message
        s.messages.append({
            'role': 'user',
            'content': f"{mode_name} 시작: {topic}\n(참여 모델: {model_names})",
            'timestamp': int(time.time())
        })
        s.save()

        stream_id = uuid.uuid4().hex
        q = queue.Queue()
        with STREAMS_LOCK:
            STREAMS[stream_id] = q

        with _active_debates_lock:
            _active_debates[session_id] = {
                "stream_id": stream_id,
                "topic": topic,
                "models": models,
                "mode": mode,
                "max_turns": max_turns,
                "current_turn": 0,
                "moderator_model": moderator_model,
                "judge_model": judge_model,
                "current_round": 1,
                "round1_responses": {},
                "round2_responses": {},
                "round2_failed": [],
                "judge_response": "",
                "history": [],
                "cancelled": False,
                "completed": False
            }

        thr = threading.Thread(
            target=_run_debate_round_thread,
            args=(session_id,),
            daemon=True
        )
        thr.start()

        return j(handler, {
            'ok': True,
            'stream_id': stream_id,
            'session_id': session_id,
            'mode': mode
        })
    except Exception as e:
        traceback.print_exc()
        return bad(handler, f"Internal server error: {e}", 500)


def handle_post_debate_next(handler, body) -> bool:
    """POST /api/debate/next — 다음 라운드 또는 발언 진행"""
    try:
        session_id = body.get('session_id')
        if not session_id:
            return bad(handler, 'session_id is required')

        with _active_debates_lock:
            state = _active_debates.get(session_id)

        if not state:
            return bad(handler, '진행 중인 토론/회의가 없습니다.')

        if state.get("completed"):
            return bad(handler, '이미 완료된 토론/회의입니다.')

        # Spawn thread for next round/turn
        thr = threading.Thread(
            target=_run_debate_round_thread,
            args=(session_id,),
            daemon=True
        )
        thr.start()

        return j(handler, {'ok': True, 'stream_id': state['stream_id'], 'mode': state.get('mode', 'debate')})
    except Exception as e:
        traceback.print_exc()
        return bad(handler, f"Internal server error: {e}", 500)


def handle_post_debate_cancel(handler, body) -> bool:
    """POST /api/debate/cancel — 토론/회의 중단"""
    try:
        session_id = body.get('session_id')
        if not session_id:
            return bad(handler, 'session_id is required')

        with _active_debates_lock:
            state = _active_debates.get(session_id)
            if state:
                state["cancelled"] = True

        if state:
            stream_id = state.get("stream_id")
            q = STREAMS.get(stream_id)
            if q:
                try:
                    q.put_nowait(('cancel', {'message': '토론/회의가 사용자에 의해 중단되었습니다.'}))
                except Exception:
                    pass

        return j(handler, {'ok': True, 'message': '토론/회의가 중단되었습니다.'})
    except Exception as e:
        traceback.print_exc()
        return bad(handler, f"Internal server error: {e}", 500)
