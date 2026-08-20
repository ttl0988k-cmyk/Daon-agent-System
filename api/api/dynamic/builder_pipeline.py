"""
builder_pipeline.py — 갭 E-L2 -> E-L4 프로덕션 배선: Builder 핸드오프 파싱 + 편입.

E-L2 Builder 서브팀이 완료되면 그 final_output 에 제작한 DRAFT 산출물을 설명하는
기계 판독 핸드오프 마커를 남긴다. 이 모듈은 그 마커를 파싱해 아티팩트를 구성하고,
E-L4 거버넌스 파이프라인(run_incorporation: 진입 -> 프로브 검증 -> 승인 -> 편입)에
올린다.

불변 순서(생성 -> 격리 -> 검증 -> 승인 -> 편입 -> 사용) 중 이 모듈이 담당하는 것은
검증/승인/편입 단계의 호출이며, 순서 강제 자체는 incorporation.run_incorporation이
보장한다.

모든 함수는 절대 raise 하지 않는다: 실패는 잘 정의된 반환값(빈 dict/None/오류
레코드)으로 전달된다.
"""

import json

from api.dynamic.logging_utils import get_logger
from api.dynamic.builder_agent import BUILD_TARGET_SKILL, DISPATCH_SPAWNED
from api.dynamic.incorporation import run_incorporation

_log = get_logger(__name__)

# Builder 미션이 서브팀에게 남기도록 지시하는 핸드오프 마커.
HANDOFF_MARKER = "[BUILDER_HANDOFF]"


def _extract_json_object(text):
    """text 안에서 첫 번째 균형 잡힌 JSON 객체 부분 문자열을 찾는다.

    찾지 못하면 None. 절대 raise 하지 않는다.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_builder_handoff(final_output):
    """Builder final_output 에서 핸드오프 dict 를 추출한다.

    HANDOFF_MARKER 뒤에 오는 JSON 객체를 파싱한다. 반환은 항상 dict
    (마커 없음/파싱 실패 시 빈 dict). 절대 None 이나 raise 하지 않는다.
    """
    text = str(final_output or "")
    if not text or HANDOFF_MARKER not in text:
        return {}
    try:
        rest = text.split(HANDOFF_MARKER, 1)[1]
        blob = _extract_json_object(rest)
        if not blob:
            return {}
        data = json.loads(blob)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        _log.warning("parse_builder_handoff 실패: %s", e)
        return {}


def _resolve_probe_path(probe_path, workspace):
    """상대 프로브 경로를 워크스페이스 기준으로 해석한다. 절대 raise 하지 않는다."""
    try:
        from pathlib import Path
        pp = Path(str(probe_path))
        if pp.is_absolute():
            return str(pp)
        if workspace:
            return str(Path(str(workspace)) / str(probe_path))
        return str(probe_path)
    except Exception:
        return str(probe_path)


def build_artifact_from_dispatch(dispatch, workspace=None):
    """스폰된 dispatch 레코드 하나로 E-L4 아티팩트 dict 를 구성한다.

    조건: status == spawned 이고 build_target == skill 이어야 편입 대상이다.
    핸드오프 마커에서 name/probe_paths 를 읽고, 없으면 능력 이름에서 파생한다.
    반환: 아티팩트 dict 또는 None (대상 아님/이름 파생 불가). 절대 raise 하지 않는다.
    """
    if not isinstance(dispatch, dict):
        return None
    if dispatch.get("status") != DISPATCH_SPAWNED:
        return None
    if str(dispatch.get("build_target") or "") != BUILD_TARGET_SKILL:
        return None
    handoff = parse_builder_handoff(dispatch.get("final_output") or "")
    cap = str(dispatch.get("capability") or "").strip()
    name = str(handoff.get("name") or "").strip()
    if not name:
        if not cap:
            return None
        try:
            from api.dynamic.skill_extractor import _sanitize_skill_name
            name = _sanitize_skill_name(cap)
        except Exception:
            name = ""
    if not name:
        return None
    raw_probes = handoff.get("probe_paths") or []
    if isinstance(raw_probes, str):
        raw_probes = [raw_probes]
    probe_paths = []
    for p in raw_probes:
        p = str(p or "").strip()
        if p:
            probe_paths.append(_resolve_probe_path(p, workspace))
    status = str(handoff.get("status") or "draft").strip().lower() or "draft"
    return {
        "name": name,
        "status": status,
        "probe_paths": probe_paths,
        "capability": cap,
        "child_run_id": dispatch.get("child_run_id"),
    }


def incorporate_builder_dispatches(dispatches, session_id=None, workspace=None,
                                   approver=None, probe_runner=None, promoter=None,
                                   log_callback=None):
    """스폰된 skill dispatch 각각을 E-L4 거버넌스 파이프라인으로 편입 시도한다.

    반환: 편입 결과 레코드 목록 (고려된 dispatch 당 하나). 절대 raise 하지 않는다.
    각 결과는 run_incorporation 의 결과 dict 에 capability/child_run_id 를 덧붙인다.
    """
    results = []
    if not dispatches:
        return results
    for dispatch in dispatches:
        artifact = build_artifact_from_dispatch(dispatch, workspace=workspace)
        if artifact is None:
            continue
        name = artifact.get("name") or ""
        if log_callback:
            try:
                log_callback("Governance",
                             "편입 거버넌스 시작: 초안 '%s' (진입 -> 프로브 -> 승인 -> 편입)" % name,
                             "running")
            except Exception:
                pass
        try:
            result = run_incorporation(
                artifact, probe_runner=probe_runner, approver=approver,
                promoter=promoter)
        except Exception as e:
            result = {"ok": False, "status": "error", "name": name,
                      "stages": [], "reason": "run_incorporation raised: %s" % e}
        if not isinstance(result, dict):
            result = {"ok": False, "status": "error", "name": name,
                      "stages": [], "reason": "run_incorporation returned non-dict"}
        result["capability"] = str(dispatch.get("capability") or "")
        result["child_run_id"] = dispatch.get("child_run_id")
        results.append(result)
        if log_callback:
            try:
                log_callback("Governance",
                             "편입 거버넌스 결과: '%s' -> %s (%s)" % (
                                 name, result.get("status"), result.get("reason")),
                             "running" if result.get("ok") else "warning")
            except Exception:
                pass
    return results


__all__ = [
    "HANDOFF_MARKER",
    "parse_builder_handoff",
    "build_artifact_from_dispatch",
    "incorporate_builder_dispatches",
]
