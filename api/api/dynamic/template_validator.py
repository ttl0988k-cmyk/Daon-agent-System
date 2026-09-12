# -*- coding: utf-8 -*-
"""
template_validator.py — 정적 에이전트 템플릿 스키마 집행기 (Schema Enforcer).

배경 (2026-09-12 진단):
  api/agents/_schema.yaml (221줄, v1.2) 은 계약을 매우 상세히 정의하지만,
  이 파일을 읽어 강제하는 코드가 저장소에 0개였다.
    - template_loader.load_template() : safe_load 후 검증 없이 캐시
    - plan_validator.validate_plan_schema() : 템플릿 노드를 skip
    - skill_registry.load_skills() : 없는 스킬을 경고만 찍고 조용히 무시
  결과: skills 필드에 실재하지 않는 이름, related_agents 에 카테고리명,
  tools 에 허용값 밖 값이 들어가도 아무도 잡지 않는다.

이 모듈은 Codex codex-rs/core/src/agent/role.rs 의
"roles may reduce capabilities but never replace the parent session's authority"
불변식을 DAON 템플릿 계층에 대응시킨 것이다:

  1. 스키마 준수        — _schema.yaml 의 required/enum/allowed_values/range 강제
  2. 참조 무결성        — skills 는 실재 스킬, related_agents 는 실재 템플릿 id
  3. 권한 불변식        — 템플릿은 부모(AIAgent) 예산을 초과할 수 없다
  4. 자기 모순          — avoid_when 이 배제하는 축인데 capability_score 가 높은 경우
  5. 계약 무결성        — 스키마/템플릿 자체가 로드되지 않으면 조용히 통과시키지 않는다

설계 원칙:
- 순수 부가(pure additive). 기존 모듈을 import 하지 않고 수정하지도 않는다.
- 절대 raise 하지 않는다. 결과는 항상 dict(list 형태의 위반 목록).
- 읽기 전용. 파일을 쓰지 않는다.

사용:
    from api.dynamic.template_validator import validate_all_templates
    report = validate_all_templates()
    # report["violations"] -> [{file, agent_id, code, field, detail}, ...]

CLI:
    PYTHONIOENCODING=utf-8 python api/api/dynamic/template_validator.py
    PYTHONIOENCODING=utf-8 python api/api/dynamic/template_validator.py --json
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from pathlib import Path

_log_prefix = "[TemplateValidator]"

# ── 부모(AIAgent) 권한 상한 — run_agent.py 실측값 ─────────────────────────
# AIAgent(max_iterations=90) 기본. 템플릿은 이 상한을 초과할 수 없다.
PARENT_MAX_ITERATIONS = 90
PARENT_MAX_TOKENS = 65536  # 보수적 상한

# ── avoid_when 태그 -> 배제되는 역량 축 (실제 모순만) ────────────────────
# 주의: frontend_only_task / native_mobile_app 등은 '도메인' 태그이며 역량 축이
# 아니다. coding=9 인 백엔드 개발자가 frontend_only_task 를 피하는 것은 정상이다.
# 아래는 "그 역량 축을 쓰는 일 자체"를 배제하는 태그만 등록한다.
_AVOID_TO_AXIS = {
    "design_work": ["design"],
    "visual_design": ["design"],
    "ui_design": ["design"],
    "documentation_only": ["documentation"],
    "technical_writing": ["documentation"],
    "devops_deployment": ["devops"],
    "infrastructure": ["devops"],
    "ci_cd": ["devops"],
    "architecture_only": ["architecture"],
    "system_design": ["architecture"],
    "testing_task": ["testing"],
    "qa_only": ["testing"],
    "debugging_task": ["debugging"],
    "reasoning_heavy": ["reasoning"],
}
_CONTRADICTION_SCORE = 8  # 이 점수 이상이면 자기모순으로 판정


# ── 경로 해석 ────────────────────────────────────────────────────────────

def _resolve_agents_dir() -> Path | None:
    """api/agents 디렉터리를 찾는다. 없으면 None."""
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent / "agents",   # api/api/dynamic -> api/agents
        Path.cwd() / "api" / "agents",
        Path.cwd() / "agents",
    ]
    for c in candidates:
        if c.is_dir() and (c / "_schema.yaml").exists():
            return c
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _load_yaml(path: Path):
    try:
        import yaml
    except Exception:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _read_schema(agents_dir: Path) -> dict:
    """_schema.yaml 을 읽는다. 실패 시 빈 dict (fail-open).

    주의: 스키마 자체가 로드되지 않은 경우 빈 dict 가 되므로, 호출자
    (validate_all_templates)가 이를 '위반 0건'으로 오인하지 않도록
    SCHEMA_LOAD_FAILED 위반을 별도로 추가한다.
    """
    p = agents_dir / "_schema.yaml"
    if not p.is_file():
        return {}
    data = _load_yaml(p)
    return data if isinstance(data, dict) else {}


# ── 자원 인덱스 ──────────────────────────────────────────────────────────

def _skill_root_candidates(agents_dir: "Path | None" = None) -> list:
    """스킬 루트 후보를 가능한 한 모두 모은다.

    ⚠ [버그 수정 2026-09-12] 이전 구현은 `Path(__file__).parent*4 / "skills"` 하나만
    썼는데, 이는 소스 트리(`<repo>/api/api/dynamic/` → `<repo>/skills`)에서만 맞고
    PyInstaller 번들(`_MEIPASS/api/dynamic/` → `daon_runtime/skills` ≠ `_MEIPASS/skills`)
    에서는 한 단계 어긋나 스킬 164건을 거짓 누락으로 보고했다(실측: 소스 304개 vs
    번들 281개 인덱싱 → 거짓 FAIL 164건). 후보를 전부 훑어 존재하는 것만 쓰도록 고친다.
    """
    cands: list = []

    # 1) Hermes 프로필(사용자 스킬) + 전역
    try:
        home = Path(os.path.expanduser("~"))
        cands.append(home / ".hermes" / "profiles" / "raon" / "skills")
        cands.append(home / ".hermes" / "skills")
    except Exception:
        pass

    # 2) PyInstaller onefile 번들 루트 (추출 디렉터리)
    try:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(Path(meipass) / "skills")
    except Exception:
        pass

    # 3) 이 파일에서 위로 올라가며 <조상>/skills
    try:
        here = Path(__file__).resolve()
        for anc in list(here.parents)[:6]:
            cands.append(anc / "skills")
    except Exception:
        pass

    # 4) agents_dir 기준 (스키마가 있는 트리 옆의 skills)
    if agents_dir is not None:
        try:
            cands.append(Path(agents_dir).parent / "skills")
            cands.append(Path(agents_dir).parent.parent / "skills")
        except Exception:
            pass

    # 5) cwd 기준
    try:
        cands.append(Path.cwd() / "skills")
    except Exception:
        pass

    seen = set()
    out: list = []
    for c in cands:
        try:
            key = str(c.resolve()).lower()
        except Exception:
            key = str(c).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if c.is_dir():
                out.append(c)
        except Exception:
            continue
    return out


def _collect_existing_skills(agents_dir: "Path | None" = None) -> set:
    """실재 스킬 id 집합. (Hermes 프로필 + 번들/소스 트리 skills 를 전부 훑는다)"""
    names: set = set()
    for base in _skill_root_candidates(agents_dir):
        try:
            for p in base.rglob("SKILL.md"):
                names.add(p.parent.name.lower())
                try:
                    head = p.read_text(encoding="utf-8", errors="ignore")[:600]
                    m = re.search(r"^name:\s*(.+)$", head, re.M)
                    if m:
                        names.add(m.group(1).strip().strip("\"'").lower())
                except Exception:
                    pass
        except Exception:
            continue
    return names


def _collect_templates(agents_dir: Path) -> dict[str, dict]:
    """{id: template_dict} — _ 로 시작하는 파일은 제외."""
    out: dict[str, dict] = {}
    try:
        for f in sorted(agents_dir.rglob("*.yaml")):
            if f.name.startswith("_"):
                continue
            d = _load_yaml(f)
            if isinstance(d, dict) and d.get("id"):
                d["_file"] = str(f)
                out[str(d["id"])] = d
    except Exception:
        pass
    return out


# ── 스키마 기반 필수/열거 검사 ──────────────────────────────────────────

def _check_schema_compliance(agent_id: str, tpl: dict, schema: dict) -> list[dict]:
    """_schema.yaml 의 fields 정의를 실제로 강제한다."""
    v: list[dict] = []
    fields = (schema or {}).get("fields") or {}
    if not isinstance(fields, dict) or not fields:
        return v

    for fname, fspec in fields.items():
        if not isinstance(fspec, dict):
            continue
        required = bool(fspec.get("required"))
        present = fname in tpl and tpl.get(fname) not in (None, "", [], {})

        if required and not present:
            v.append({"agent_id": agent_id, "code": "SCHEMA_REQUIRED",
                      "field": fname, "detail": "required 필드 누락"})
            continue
        if not present:
            continue

        val = tpl.get(fname)

        # enum
        enum = fspec.get("enum")
        if isinstance(enum, list) and val not in enum:
            v.append({"agent_id": agent_id, "code": "SCHEMA_ENUM", "field": fname,
                      "detail": "'%s' not in %s" % (val, enum)})

        # allowed_values (list[string] 필드용)
        allowed = fspec.get("allowed_values")
        if isinstance(allowed, list) and isinstance(val, list):
            clean_allowed = [str(a).split("#")[0].strip() for a in allowed]
            for item in val:
                if item not in clean_allowed:
                    v.append({"agent_id": agent_id, "code": "SCHEMA_ALLOWED", "field": fname,
                              "detail": "'%s' not in %s" % (item, clean_allowed)})

        # range (integer)
        rng = fspec.get("range")
        if isinstance(rng, list) and len(rng) == 2 and isinstance(val, int):
            if not (rng[0] <= val <= rng[1]):
                v.append({"agent_id": agent_id, "code": "SCHEMA_RANGE", "field": fname,
                          "detail": "%s not in [%s, %s]" % (val, rng[0], rng[1])})

        # object 하위 필드 (capability_score, cost_profile, runtime, model_prefs)
        sub = fspec.get("fields")
        if isinstance(sub, dict) and isinstance(val, dict):
            for sfname, sspec in sub.items():
                if not isinstance(sspec, dict):
                    continue
                sval = val.get(sfname)
                srng = sspec.get("range")
                if isinstance(srng, list) and len(srng) == 2 and isinstance(sval, int):
                    if not (srng[0] <= sval <= srng[1]):
                        v.append({"agent_id": agent_id, "code": "SCHEMA_RANGE",
                                  "field": "%s.%s" % (fname, sfname),
                                  "detail": "%s not in [%s, %s]" % (sval, srng[0], srng[1])})
                senum = sspec.get("enum")
                if isinstance(senum, list) and sval is not None and sval not in senum:
                    v.append({"agent_id": agent_id, "code": "SCHEMA_ENUM",
                              "field": "%s.%s" % (fname, sfname),
                              "detail": "'%s' not in %s" % (sval, senum)})
    return v


# ── 개별 검사기 ──────────────────────────────────────────────────────────

def _check_reference_integrity(agent_id: str, tpl: dict,
                               templates: dict, skills: set[str]) -> list[dict]:
    """skills / related_agents 참조가 실재하는가. (Codex 권한 무결성 대응)"""
    v: list[dict] = []
    for s in (tpl.get("skills") or []):
        if str(s).strip().lower() not in skills:
            v.append({"agent_id": agent_id, "code": "REF_SKILL_MISSING",
                      "field": "skills", "detail": "실재하지 않는 스킬 '%s'" % s})
    for r in (tpl.get("related_agents") or []):
        if r not in templates:
            v.append({"agent_id": agent_id, "code": "REF_AGENT_MISSING",
                      "field": "related_agents",
                      "detail": "실재하지 않는 에이전트 id '%s'" % r})
    return v


def _check_authority_invariant(agent_id: str, tpl: dict) -> list[dict]:
    """템플릿은 부모(AIAgent) 예산을 초과할 수 없다.

    Codex role.rs: 'never replace the parent session's authority'.
    """
    v: list[dict] = []
    rt = tpl.get("runtime") or {}
    if not isinstance(rt, dict):
        return v
    mi = rt.get("max_iterations")
    if isinstance(mi, int) and mi > PARENT_MAX_ITERATIONS:
        v.append({"agent_id": agent_id, "code": "AUTHORITY_ITERATIONS",
                  "field": "runtime.max_iterations",
                  "detail": "%s > 부모 상한 %s" % (mi, PARENT_MAX_ITERATIONS)})
    mt = rt.get("max_tokens")
    if isinstance(mt, int) and mt > PARENT_MAX_TOKENS:
        v.append({"agent_id": agent_id, "code": "AUTHORITY_TOKENS",
                  "field": "runtime.max_tokens",
                  "detail": "%s > 부모 상한 %s" % (mt, PARENT_MAX_TOKENS)})
    tp = rt.get("temperature")
    if isinstance(tp, (int, float)) and not (0 <= tp <= 2):
        v.append({"agent_id": agent_id, "code": "AUTHORITY_TEMP",
                  "field": "runtime.temperature",
                  "detail": "%s not in [0, 2]" % tp})
    return v


def _check_self_contradiction(agent_id: str, tpl: dict) -> list[dict]:
    """avoid_when 이 배제하는 축인데 capability_score 가 높은 자기모순."""
    v: list[dict] = []
    aw = set(tpl.get("avoid_when") or [])
    cs = tpl.get("capability_score") or {}
    if not isinstance(cs, dict):
        return v
    for tag, axes in _AVOID_TO_AXIS.items():
        if tag in aw:
            for ax in axes:
                score = cs.get(ax)
                if isinstance(score, int) and score >= _CONTRADICTION_SCORE:
                    v.append({"agent_id": agent_id, "code": "SELF_CONTRADICTION",
                              "field": "avoid_when/capability_score",
                              "detail": "avoid_when 에 '%s' 인데 %s=%s" % (tag, ax, score)})
    return v


def _check_uniqueness(templates: dict) -> list[dict]:
    """id 중복 검사 (dict 라 이미 병합되므로 병합 전 파일 기준)."""
    v: list[dict] = []
    seen: dict[str, str] = {}
    for aid, tpl in templates.items():
        f = tpl.get("_file", "")
        if aid in seen and seen[aid] != f:
            v.append({"agent_id": aid, "code": "DUP_ID", "field": "id",
                      "detail": "%s 와 %s 중복" % (seen[aid], f)})
        seen[aid] = f
    return v


# ── 공개 API ─────────────────────────────────────────────────────────────

def validate_all_templates(agents_dir: "str | Path | None" = None) -> dict:
    """모든 정적 에이전트 템플릿을 검증한다.

    절대 raise 하지 않는다. 반환:
      {
        "agents_dir": str,
        "schema_loaded": bool,
        "schema_version": str | None,
        "total": int,
        "skills_indexed": int,
        "violations": [ {file, agent_id, code, field, detail}, ... ],
        "by_code": {code: count},
        "by_agent": {agent_id: count},
        "ok": bool,
      }
    """
    try:
        base = Path(agents_dir) if agents_dir else _resolve_agents_dir()
        if base is None:
            return {"agents_dir": "", "schema_loaded": False, "total": 0,
                    "skills_indexed": 0, "violations": [], "by_code": {},
                    "by_agent": {}, "ok": False,
                    "error": "agents directory not found"}

        schema = _read_schema(base)
        templates = _collect_templates(base)
        skills = _collect_existing_skills()

        violations: list[dict] = []

        # ── 계약 무결성: 스키마/템플릿 자체가 살아있는지 먼저 본다.
        # 이게 없으면 스키마가 깨진 상태에서도 '위반 0건'으로 조용히 PASS 를
        # 반환하는 위험이 생긴다 (probe 가 적발한 결함).
        if not base.is_dir():
            violations.append({"agent_id": "-", "code": "AGENTS_DIR_MISSING",
                               "field": "agents_dir", "file": "",
                               "detail": "템플릿 디렉토리가 없음: %s" % base})
        schema_file = base / "_schema.yaml"
        if schema_file.is_file() and not schema:
            violations.append({"agent_id": "-", "code": "SCHEMA_LOAD_FAILED",
                               "field": "_schema.yaml", "file": str(schema_file),
                               "detail": "스키마 YAML 파싱 실패 — 스키마 기반 검사가 전부 건너뛰었다"})
        if not templates and base.is_dir():
            violations.append({"agent_id": "-", "code": "NO_TEMPLATES",
                               "field": "agents_dir", "file": "",
                               "detail": "템플릿을 하나도 로드하지 못했다: %s" % base})

        violations += _check_uniqueness(templates)

        for aid, tpl in templates.items():
            fpath = tpl.get("_file", "")
            found = []
            found += _check_schema_compliance(aid, tpl, schema)
            found += _check_reference_integrity(aid, tpl, templates, skills)
            found += _check_authority_invariant(aid, tpl)
            found += _check_self_contradiction(aid, tpl)
            for item in found:
                item["file"] = fpath
                violations.append(item)

        by_code: dict[str, int] = {}
        by_agent: dict[str, int] = {}
        for item in violations:
            by_code[item["code"]] = by_code.get(item["code"], 0) + 1
            by_agent[item["agent_id"]] = by_agent.get(item["agent_id"], 0) + 1

        return {
            "agents_dir": str(base),
            "schema_loaded": bool(schema),
            "schema_version": (schema or {}).get("schema_version"),
            "total": len(templates),
            "skills_indexed": len(skills),
            "violations": violations,
            "by_code": by_code,
            "by_agent": by_agent,
            "ok": len(violations) == 0,
        }
    except Exception as exc:  # 절대 raise 금지
        return {"agents_dir": "", "schema_loaded": False, "total": 0,
                "skills_indexed": 0, "violations": [], "by_code": {},
                "by_agent": {}, "ok": False, "error": str(exc)}


def validate_template(template: dict, templates: dict | None = None,
                      skills: set[str] | None = None,
                      schema: dict | None = None) -> list[dict]:
    """단일 템플릿 검증 (런타임 배선용 경량 진입점).

    template_loader 가 load_template() 직후 호출한다. 실패해도 절대 raise 하지
    않고 빈 리스트를 반환한다(fail-open) — 검증 실패가 에이전트 실행을
    막아서는 안 되기 때문이다.
    """
    try:
        if not isinstance(template, dict) or not template.get("id"):
            return []
        aid = str(template["id"])
        if schema is None:
            base = _resolve_agents_dir()
            schema = _read_schema(base) if base else {}
        if templates is None:
            base = _resolve_agents_dir()
            templates = _collect_templates(base) if base else {aid: template}
        if skills is None:
            skills = _collect_existing_skills()

        out: list[dict] = []
        out += _check_schema_compliance(aid, template, schema or {})
        out += _check_reference_integrity(aid, template, templates or {}, skills or set())
        out += _check_authority_invariant(aid, template)
        out += _check_self_contradiction(aid, template)
        return out
    except Exception:
        return []


def main() -> int:
    as_json = "--json" in sys.argv
    report = validate_all_templates()
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1

    print("=" * 70)
    print("%s DAON 정적 에이전트 템플릿 스키마 집행" % _log_prefix)
    print("=" * 70)
    print("agents dir      : %s" % report["agents_dir"])
    print("schema loaded   : %s (v%s)" % (report["schema_loaded"], report.get("schema_version")))
    print("templates       : %s" % report["total"])
    print("skills indexed  : %s" % report["skills_indexed"])
    print("violations      : %s" % len(report["violations"]))
    print()
    if report["violations"]:
        print("── 코드별 ──")
        for code, cnt in sorted(report["by_code"].items(), key=lambda x: -x[1]):
            print("  %-26s %s" % (code, cnt))
        print()
        print("── 상세 (최대 60건) ──")
        for item in report["violations"][:60]:
            print("  [%s] %s :: %s" % (item["code"], item["agent_id"], item["field"]))
            print("        %s" % item["detail"])
        if len(report["violations"]) > 60:
            print("  ... 외 %d건" % (len(report["violations"]) - 60))
    else:
        print("위반 없음")
    print()
    print("RESULT:", "PASS" if report.get("ok") else "FAIL")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
