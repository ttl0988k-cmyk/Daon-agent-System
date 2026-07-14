"""
Daon Agent System — Expert Debate Mode routes.
"""
import logging
import threading
import time
import queue
import uuid
import traceback
from pathlib import Path

from api.helpers import j, bad
from api.models import get_session
from api.config import STREAMS, STREAMS_LOCK

_logger = logging.getLogger(__name__)

# Global active debates state: session_id -> debate state dictionary
_active_debates = {}
_active_debates_lock = threading.Lock()


def _get_model_label(model_id):
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
    return model_id.split('/')[-1].replace('-', ' ').replace('_', ' ').title()


def _execute_debate_llm(model_id, system_prompt, user_prompt, stream_fn) -> str:
    from api.config import resolve_model_provider
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from agent.auxiliary_client import call_llm
    
    _model, _provider, _base_url = resolve_model_provider(model_id)
    _rt = resolve_runtime_provider(requested=_provider)
    _api_key = _rt.get("api_key")
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
    
    # Determine provider and base URL for call_llm
    if _provider == 'minimax':
        call_provider = 'custom'
        _base_url = 'https://api.minimax.io/v1'
    else:
        call_provider = _provider
        
    print(f"[Debate Debug] Calling LLM with model_id={model_id!r} -> resolved: provider={_provider!r}, model={_model!r}, base_url={_base_url!r}, key_len={len(_api_key) if _api_key else 0}")
    
    try:
        resp = call_llm(
            provider=call_provider,
            model=_model,
            base_url=_base_url,
            api_key=_api_key,
            messages=messages
        )
    except Exception as e:
        print(f"[Debate Debug] call_llm failed with exception: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise e
        
    content = resp.choices[0].message.content or ""
    
    # Fake-stream chunking
    chunk_size = 6
    delay = 0.012
    for i in range(0, len(content), chunk_size):
        chunk = content[i:i+chunk_size]
        stream_fn(chunk)
        time.sleep(delay)
        
    return content


def _run_debate_round_thread(session_id):
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

    s = get_session(session_id)
    
    try:
        # Round 1
        if state["current_round"] == 1:
            put('debate_status', {'text': '1라운드: 각 모델의 주장 수집 중...'})
            
            for model_id in state["models"]:
                if state["cancelled"]:
                    break
                
                model_label = _get_model_label(model_id)
                put('debate_status', {'text': f'1라운드: {model_label}의 주장 생성 중...'})
                
                content = _execute_debate_llm(
                    model_id=model_id,
                    system_prompt=(
                        "당신은 전문가 토론의 참여자입니다. 주어진 주제에 대해 본인의 고유한 분석과 주장을 마크다운(Markdown) 포맷으로 작성해 주세요.\n"
                        "중복된 마크다운 코드 블록으로 전체 글을 감싸지 말고 일반 마크다운 글로 작성해 주세요. 반드시 한국어로 작성해야 합니다."
                    ),
                    user_prompt=f"토론 주제: {state['topic']}",
                    stream_fn=lambda token: put('debate_token', {
                        'sender': f"🤖 {model_label} (주장)",
                        'text': token
                    })
                )
                
                state["round1_responses"][model_id] = content
                
                msg = {
                    'role': 'assistant',
                    'content': content,
                    'sender': f"🤖 {model_label} (주장)",
                    'is_debate': True,
                    'timestamp': int(time.time())
                }
                s.messages.append(msg)
                s.save()
                
                put('debate_message_done', {'sender': f"🤖 {model_label} (주장)"})
                time.sleep(0.5)
            
            if not state["cancelled"]:
                # State transitions to next round waiting
                state["current_round"] = 2
                put('debate_status', {'text': '1라운드 완료. 다음 라운드(반박) 진행 버튼을 눌러주세요.', 'waiting_next': True})
                put('done', {'session': s.compact() | {'messages': s.messages}})
                
        # Round 2
        elif state["current_round"] == 2:
            put('debate_status', {'text': '2라운드: 상호 반박 수집 중...'})
            
            for model_id in state["models"]:
                if state["cancelled"]:
                    break
                
                model_label = _get_model_label(model_id)
                put('debate_status', {'text': f'2라운드: {model_label}의 반박문 생성 중...'})
                
                others_text = ""
                for m_id, resp in state["round1_responses"].items():
                    if m_id != model_id:
                        others_text += f"=== {_get_model_label(m_id)}의 주장 ===\n{resp}\n\n"
                        
                content = _execute_debate_llm(
                    model_id=model_id,
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
                    stream_fn=lambda token: put('debate_token', {
                        'sender': f"💬 {model_label} (반박)",
                        'text': token
                    })
                )
                
                state["round2_responses"][model_id] = content
                
                msg = {
                    'role': 'assistant',
                    'content': content,
                    'sender': f"💬 {model_label} (반박)",
                    'is_debate': True,
                    'timestamp': int(time.time())
                }
                s.messages.append(msg)
                s.save()
                
                put('debate_message_done', {'sender': f"💬 {model_label} (반박)"})
                time.sleep(0.5)
                
            if not state["cancelled"]:
                state["current_round"] = 3
                put('debate_status', {'text': '2라운드 완료. 최종 판결 요청 버튼을 눌러주세요.', 'waiting_next': True})
                put('done', {'session': s.compact() | {'messages': s.messages}})
                
        # Round 3
        elif state["current_round"] == 3:
            put('debate_status', {'text': '최종 판결: 판결문 및 계획안 생성 중...'})
            
            judge_model_id = s.model
            judge_label = _get_model_label(judge_model_id)
            
            transcript = ""
            for m_id in state["models"]:
                m_lbl = _get_model_label(m_id)
                r1 = state["round1_responses"].get(m_id, "")
                r2 = state["round2_responses"].get(m_id, "")
                transcript += f"■ {m_lbl} (1라운드 주장):\n{r1}\n\n"
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
                    'text': token
                })
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
            
            put('debate_message_done', {'sender': f"⚖️ 판사 ({judge_label})"})
            
            state["current_round"] = 4
            put('debate_status', {'text': '토론 및 최종 판결 완료.', 'completed': True})
            put('done', {'session': s.compact() | {'messages': s.messages}})
            
    except Exception as e:
        traceback.print_exc()
        put('error', {'message': f'토론 진행 중 오류 발생: {str(e)}'})


# ── POST route helpers ────────────────────────────────────────────────────────

def handle_post_debate_start(handler, body) -> bool:
    """POST /api/debate/start — 시작"""
    try:
        session_id = body.get('session_id')
        topic = body.get('topic', '').strip()
        models = body.get('models', [])
        
        if not session_id or not topic or not models:
            return bad(handler, 'session_id, topic, and models are required')
            
        if len(models) < 2:
            return bad(handler, '최소 2개 이상의 모델을 선택해야 합니다.')
            
        s = get_session(session_id)
        if not s:
            return bad(handler, 'Session not found', 404)
            
        # Append User Topic to Session Messages
        s.messages.append({
            'role': 'user',
            'content': f"⚖️ 토론 시작: {topic}\n(참여 모델: {', '.join([_get_model_label(m) for m in models])})",
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
                "current_round": 1,
                "round1_responses": {},
                "round2_responses": {},
                "judge_response": "",
                "cancelled": False
            }
            
        thr = threading.Thread(
            target=_run_debate_round_thread,
            args=(session_id,),
            daemon=True
        )
        thr.start()
        
        return j(handler, {'ok': True, 'stream_id': stream_id, 'session_id': session_id})
    except Exception as e:
        traceback.print_exc()
        return bad(handler, f"Internal server error: {e}", 500)


def handle_post_debate_next(handler, body) -> bool:
    """POST /api/debate/next — 다음 라운드 진행"""
    try:
        session_id = body.get('session_id')
        if not session_id:
            return bad(handler, 'session_id is required')
            
        with _active_debates_lock:
            state = _active_debates.get(session_id)
            
        if not state:
            return bad(handler, '진행 중인 토론이 없습니다.')
            
        # Spawn thread for next round
        thr = threading.Thread(
            target=_run_debate_round_thread,
            args=(session_id,),
            daemon=True
        )
        thr.start()
        
        return j(handler, {'ok': True, 'stream_id': state['stream_id']})
    except Exception as e:
        traceback.print_exc()
        return bad(handler, f"Internal server error: {e}", 500)


def handle_post_debate_cancel(handler, body) -> bool:
    """POST /api/debate/cancel — 토론 중단"""
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
                    q.put_nowait(('cancel', {}))
                except Exception:
                    pass
                    
        return j(handler, {'ok': True, 'message': '토론이 중단되었습니다.'})
    except Exception as e:
        traceback.print_exc()
        return bad(handler, f"Internal server error: {e}", 500)
