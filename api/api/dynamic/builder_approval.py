"""
builder_approval.py — 갭 E-L2/E-L4 프로덕션 배선: 세션 기반 승인자 팩토리.

E-L2 Builder 스폰 게이트와 E-L4 편입 거버넌스의 승인 단계는 모두 "승인자"가
필요하다. 이 모듈은 기존 세션 승인 인프라(api.approval: set_pending /
has_pending / approve / reject / get_history) 위에 승인자를 구성한다. 그 결과:

- 프론트엔드(static/modules/approval.js)가 plan.md 승인과 동일한 카드를 그리고,
  자율 실행 토글이 켜져 있으면 추가 프론트 코드 없이 자동 승인된다.
- UI가 붙어 있지 않은 투입(API 전용)에서는 설정 가능한 타임아웃이 자동 승인해
  루프가 영원히 멈추지 않는다 (orchestrator.run의 plan.md 승인 패턴과 동일).

여기서 반환되는 모든 승인자는 ``subject -> bool`` 순수 호출 가능 객체이며
절대 raise 하지 않는다: 내부 실패는 전부 거부(fail-safe)로 처리된다.
(E-L2/E-L4 게이트 계약과 일치)
"""

import os
import time
import uuid

from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)

# 와이어 안정 승인 종류 문자열.
APPROVAL_KIND_BUILDER_SPAWN = "builder_spawn"
APPROVAL_KIND_INCORPORATION = "incorporation"

_DEFAULT_AUTO_TIMEOUT = 45
_DEFAULT_POLL_INTERVAL = 0.5


def _resolve_auto_timeout(auto_timeout):
    """자동 승인 타임아웃(초)을 해석한다. 반환이 None 이면 응답까지 무한 대기.

    우선순위: 명시 인자 -> config approvals.builder_auto_timeout ->
    approvals.file_tool_auto_timeout -> env HERMES_AUTO_APPROVE_SECONDS -> 기본 45.
    0 이하는 무한 대기(None)로 해석한다.
    """
    if auto_timeout is not None:
        try:
            value = int(auto_timeout)
        except (ValueError, TypeError):
            value = _DEFAULT_AUTO_TIMEOUT
        return value if value > 0 else None
    value = _DEFAULT_AUTO_TIMEOUT
    try:
        from hermes_cli.config import load_config as _load_hf_config
        _cfg = (_load_hf_config() or {}).get("approvals", {}) or {}
        value = int(_cfg.get(
            "builder_auto_timeout",
            _cfg.get("file_tool_auto_timeout", _DEFAULT_AUTO_TIMEOUT)))
    except Exception:
        value = _DEFAULT_AUTO_TIMEOUT
    try:
        value = int(os.getenv("HERMES_AUTO_APPROVE_SECONDS", value))
    except (ValueError, TypeError):
        pass
    return value if value > 0 else None


def _kind_label(kind):
    if kind == APPROVAL_KIND_BUILDER_SPAWN:
        return "Builder 제작"
    if kind == APPROVAL_KIND_INCORPORATION:
        return "스킬 편입"
    return "승인"


def _build_message(kind, subject):
    """승인 카드/로그에 표시할 한국어 메시지를 구성한다 (이모지/특수문자 금지)."""
    if kind == APPROVAL_KIND_BUILDER_SPAWN:
        if isinstance(subject, dict):
            cap = str(subject.get("capability") or "").strip()
        else:
            cap = str(subject or "").strip()
        return ("Builder 제작 승인 필요: 결핍 능력 '%s'에 대한 제작 서브팀을 스폰합니다. "
                "승인하면 초안(draft) 제작을 시작합니다." % (cap or "알 수 없음"))
    if kind == APPROVAL_KIND_INCORPORATION:
        name = ""
        if isinstance(subject, dict):
            name = str(subject.get("name") or "").strip()
        return ("스킬 편입 승인 필요: 초안 '%s'을(를) 프로브 검증 후 "
                "정식 스킬로 편입합니다." % (name or "알 수 없음"))
    return "승인이 필요합니다."


def make_session_approver(session_id, kind=APPROVAL_KIND_BUILDER_SPAWN,
                          run_id=None, log_callback=None, auto_timeout=None,
                          poll_interval=None):
    """세션 기반 승인자를 구성해 반환한다.

    반환 호출 가능 객체 ``subject -> bool`` 의 동작:
      1. api.approval.set_pending 으로 승인 대기를 등록한다. 이때 프론트엔드 호환
         페이로드(status='pending', is_plan=True -> 메시지 카드 렌더 + diff
         apply-preview 스킵)를 사용하고, approval_id 로 이력 상관 관계를 만든다.
      2. SSE 스트림과 다이나믹 잡 상태를 통해 승인 대기를 알린다 (최선 노력).
      3. 승인이 해결(approve/reject)되거나 자동 타임아웃(plan.md 패턴)이 발화할
         때까지 대기한다.
      4. 승인 이력(get_history)에서 approval_id 항목을 찾아 승인/거부를 판정한다.

    절대 raise 하지 않는다: 어떤 실패든 False(거부)를 반환한다.
    """
    if not session_id:
        def _no_session_approver(subject):
            _log.warning("%s approver: session_id 없음 - 기본 거부", kind)
            return False
        return _no_session_approver

    timeout = _resolve_auto_timeout(auto_timeout)
    sleep_step = _DEFAULT_POLL_INTERVAL
    if poll_interval is not None:
        try:
            sleep_step = max(0.01, float(poll_interval))
        except (ValueError, TypeError):
            sleep_step = _DEFAULT_POLL_INTERVAL

    def _approver(subject):
        approval_id = uuid.uuid4().hex[:16]
        message = _build_message(kind, subject)
        try:
            from api.approval import set_pending, has_pending, get_history
            from api.approval import approve as _do_approve
        except Exception as e:
            _log.warning("%s approver: 승인 모듈 사용 불가: %s", kind, e)
            return False

        # 1. 승인 대기 등록 (프론트엔드 호환 페이로드).
        try:
            set_pending(session_id, {
                'preview_id': '',
                'is_plan': True,
                'is_builder_approval': True,
                'approval_kind': kind,
                'approval_id': approval_id,
                'session_id': session_id,
                'source_agent': 'Builder' if kind == APPROVAL_KIND_BUILDER_SPAWN else 'Governance',
                'message': message,
                'status': 'pending',
                'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            })
        except Exception as e:
            _log.warning("%s approver: set_pending 실패: %s", kind, e)
            return False

        # 다이나믹 잡 상태를 승인 대기 표시 (하네스 폴러 표면).
        if run_id:
            try:
                from api.dynamic_jobs import set_job_awaiting_approval
                set_job_awaiting_approval(run_id, message)
            except Exception:
                pass

        # 2. SSE 알림 (최선 노력).
        queue = None
        try:
            from api.config import STREAMS
            queue = STREAMS.get(session_id)
            if queue:
                queue.put(('approval', {
                    'preview_id': '',
                    'is_plan': True,
                    'is_builder_approval': True,
                    'approval_kind': kind,
                    'approval_id': approval_id,
                    'message': message,
                    'status': 'pending',
                    'session_id': session_id,
                }))
        except Exception:
            queue = None

        if log_callback:
            try:
                agent = 'Builder' if kind == APPROVAL_KIND_BUILDER_SPAWN else 'Governance'
                log_callback(agent, message, "running")
            except Exception:
                pass

        # 3. 해결 또는 자동 타임아웃까지 대기.
        started = time.time()
        auto_done = False
        try:
            while has_pending(session_id):
                if (not auto_done and timeout is not None
                        and (time.time() - started) >= timeout):
                    auto_done = True
                    try:
                        _do_approve(session_id, reviewer="auto")
                    except Exception as e:
                        _log.warning("%s approver: 자동 승인 실패: %s", kind, e)
                    _auto_msg = "응답 없음 - %d초 후 %s 요청이 자동 승인되었습니다." % (
                        timeout, _kind_label(kind))
                    if queue:
                        try:
                            queue.put(('approval', {
                                'preview_id': '',
                                'is_plan': True,
                                'approval_id': approval_id,
                                'message': _auto_msg,
                                'status': 'auto_approved',
                                'session_id': session_id,
                            }))
                        except Exception:
                            pass
                    if log_callback:
                        try:
                            log_callback('System', _auto_msg, "running")
                        except Exception:
                            pass
                time.sleep(sleep_step)
        except Exception as e:
            _log.warning("%s approver: 대기 루프 오류: %s", kind, e)
            return False

        # 잡 상태를 다시 실행 중으로 복원.
        if run_id:
            try:
                from api.dynamic_jobs import set_job_running
                set_job_running(run_id)
            except Exception:
                pass

        # 4. 이력에서 approval_id 항목을 찾아 판정.
        try:
            hist = get_history(session_id, limit=10)
        except Exception:
            return False
        for entry in reversed(hist or []):
            if entry.get('approval_id') == approval_id:
                approved = entry.get('status') == 'approved'
                if log_callback:
                    try:
                        verb = "승인" if approved else "거부"
                        log_callback(
                            'System',
                            "%s 요청 %s됨 (approval_id=%s)" % (
                                _kind_label(kind), verb, approval_id),
                            "running")
                    except Exception:
                        pass
                return approved
        # 대응 이력 없음 -> 거부 (fail-safe).
        _log.warning("%s approver: approval_id=%s 이력 없음 - 기본 거부",
                     kind, approval_id)
        return False

    return _approver


__all__ = [
    "APPROVAL_KIND_BUILDER_SPAWN",
    "APPROVAL_KIND_INCORPORATION",
    "make_session_approver",
]
