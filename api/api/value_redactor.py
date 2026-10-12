"""Value-based secret redactor -- port of ds-bot ``secrets.js:32-78``.

설계서 ``_dsbot_ref/design/03_secrets_design.md`` §5 Phase0 항목 (2).

등록된 시크릿 *값 자체* 를 찾아 ``[secret:NAME]`` 라벨로 치환한다.
다온의 기존 패턴 기반 마스킹(``redact.py``)과 경쟁하지 않고,
패턴이 못 잡는 "등록된 임의 값"을 메우는 상호 보완 레이어이다.
적용 순서: 패턴 마스킹 → 값 기반 redactor.

원본 동작 이식 명세 (``secrets.js``):
  - ``SECRET_NAME_RE`` = ``/^[A-Z][A-Z0-9_]*$/``
  - ``normalizeName``: trim → ``$``/``env:``/``DAON_SECRET_``(``DSH_SECRET_`` 호환)
    접두 제거 → 공백·하이픈을 ``_`` 로 → 대문자
  - ``redactor(entries)``: 값 기반 치환 함수 반환
      - ``MIN_VALUE`` (4) 미만 값은 무시
      - 긴 값 우선(길이 내림차순 단일 정규식 alternation)
      - ``JSON.stringify`` 이스케이프 변형도 함께 치환
      - ``WHOLE_VALUE`` (8) 미만 짧은 값은 단독 등장만 치환
        (원본 ``(?<![\\p{L}\\p{N}])...(?![\\p{L}\\p{N}])`` 와 동등)
      - 단일 패스 — 이미 쓴 라벨은 다시 매칭되지 않음
      - 라벨 ``[secret:NAME]``
  - ``redact_text`` / ``redactDeep`` (재귀, 변경 없으면 동일 참조 반환)

표준 라이브러리(``re``/``json``)만 사용한다.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Tuple

# ---------------------------------------------------------------------------
# 상수 (secrets.js:11-18)
# ---------------------------------------------------------------------------

SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

ENV_PREFIX = "DAON_SECRET_"
# 구 접두(``DSH_SECRET_``)는 normalize 시에만 호환 제거된다.
_LEGACY_ENV_PREFIX = "DSH_SECRET_"

# 짧은 값(PIN 등)이 일반 단어에 오탐되는 것을 막는 경계.
# 원본: (?<![\p{L}\p{N}]) ... (?![\p{L}\p{N}])
# stdlib ``re`` 에는 \p 가 없으므로 [^\W_] (언더스코어 제외 단어 문자
# = 유니코드 자모+숫자 근사)로 동등 표현한다.
_SHORT_BOUNDARY_BEFORE = r"(?<![^\W_])"
_SHORT_BOUNDARY_AFTER = r"(?![^\W_])"

MIN_VALUE = 4
WHOLE_VALUE = 8
MAX_VALUE = 16384


def env_name(name: str) -> str:
    """``DAON_SECRET_<NAME>`` 셸 환경변수명을 반환한다."""
    return f"{ENV_PREFIX}{name}"


def normalizeName(raw: Any) -> str:  # noqa: N802 - 원본 JS 함수명 이식
    """원본 ``normalizeName`` 이식.

    trim → ``$``/``env:``/``DAON_SECRET_``(``DSH_SECRET_`` 호환) 접두 제거
    → 공백·하이픈을 ``_`` 로 → 대문자.
    """
    text = str(raw if raw is not None else "").strip()
    text = re.sub(r"^\$(env:)?", "", text, flags=re.IGNORECASE)
    if text[: len(ENV_PREFIX)].upper() == ENV_PREFIX:
        text = text[len(ENV_PREFIX):]
    elif text[: len(_LEGACY_ENV_PREFIX)].upper() == _LEGACY_ENV_PREFIX:
        text = text[len(_LEGACY_ENV_PREFIX):]
    text = re.sub(r"[\s-]+", "_", text)
    return text.upper()


# snake_case 별칭 (JS 이식명 + 파이썬 관례 모두 지원)
normalize_name = normalizeName


# ---------------------------------------------------------------------------
# redactor (secrets.js:32-53)
# ---------------------------------------------------------------------------

def _entry_name_value(entry: Any) -> Tuple[Any, Any]:
    if isinstance(entry, Mapping):
        return entry.get("name"), entry.get("value")
    return getattr(entry, "name", None), getattr(entry, "value", None)


def _json_escaped_variant(value: str) -> str:
    """``JSON.stringify(value).slice(1, -1)`` 대응."""
    return json.dumps(value, ensure_ascii=False)[1:-1]


def redactor(entries: Iterable[Any]) -> Callable[[str], str]:
    """등록된 모든 값을 치환하는 함수(``text -> text``)를 반환한다.

    * ``entries``: ``{"name": ..., "value": ...}`` 매핑 또는
      ``.name``/``.value`` 속성을 가진 객체들의 이터러블.
    * 값이 겹치면 긴 값 우선(길이 내림차순 단일 정규식).
    * JSON 이스케이프 변형(``"`` → ``\\"`` 등)도 함께 치환.
    * 8자 미만 짧은 값은 자모/숫자로 둘러싸이지 않은 단독 등장만 치환.
    * 단일 패스 — 이미 기록된 ``[secret:...]`` 라벨은 재매칭되지 않음.
    * 치환 대상이 없으면 항등 함수를 반환.
    """
    pairs: List[Tuple[str, str, bool]] = []
    for entry in entries or []:
        name, value = _entry_name_value(entry)
        if not isinstance(value, str) or len(value) < MIN_VALUE:
            continue
        label = f"[secret:{name}]"
        short = len(value) < WHOLE_VALUE
        pairs.append((value, label, short))
        escaped = _json_escaped_variant(value)
        if escaped != value:
            pairs.append((escaped, label, short))

    # 긴 값 우선 — alternation 은 앞에서부터 시도되므로
    # 같은 시작 위치에서는 긴 값이 먼저 매칭된다.
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    if not pairs:
        return lambda text: text

    # 동일 값 문자열은 먼저 나온(긴 쪽, 동순위면 먼저 등록된) 라벨이 이긴다.
    labels: Dict[str, str] = {}
    for value, label, _short in pairs:
        if value not in labels:
            labels[value] = label

    def _branch(value: str, short: bool) -> str:
        quoted = re.escape(value)
        if short:
            return f"{_SHORT_BOUNDARY_BEFORE}{quoted}{_SHORT_BOUNDARY_AFTER}"
        return quoted

    pattern = re.compile("|".join(_branch(v, s) for v, _, s in pairs))

    def _redact(text: str) -> str:
        if not isinstance(text, str):
            return text
        # 고속 경로 — 어떤 값이든 부분 문자열로 없으면 정규식 실행 생략.
        if not any(value in text for value, _label, _short in pairs):
            return text
        return pattern.sub(lambda match: labels.get(match.group(0), match.group(0)), text)

    return _redact


def redact_text(text: str, entries: Iterable[Any]) -> str:
    """편의 함수 — ``redactor(entries)(text)`` 와 동등."""
    return redactor(entries)(text)


# ---------------------------------------------------------------------------
# redactDeep (secrets.js:55-78)
# ---------------------------------------------------------------------------

def redact_deep(value: Any, redact: Callable[[str], str]) -> Any:
    """문자열·리스트·튜플·딕셔너리를 재귀 치환한다.

    변경이 없으면 **동일 참조** 를 반환하므로 호출자가 cheap 하게
    변경 여부를 판단할 수 있다(원본 주석 동일). 딕셔너리 키는
    치환하지 않고 값만 재귀한다(원본 ``Object.entries`` 루프와 동일).
    """
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        changed = False
        items = []
        for item in value:
            replaced = redact_deep(item, redact)
            if replaced is not item:
                changed = True
            items.append(replaced)
        return items if changed else value
    if isinstance(value, tuple):
        changed = False
        items = []
        for item in value:
            replaced = redact_deep(item, redact)
            if replaced is not item:
                changed = True
            items.append(replaced)
        return tuple(items) if changed else value
    if isinstance(value, dict):
        changed = False
        next_map: Dict[Any, Any] = {}
        for key, item in value.items():
            replaced = redact_deep(item, redact)
            if replaced is not item:
                changed = True
            next_map[key] = replaced
        return next_map if changed else value
    return value


# camelCase 별칭 (JS 이식명 호환)
redactDeep = redact_deep
redact_text_fn = redact_text
