"""Daon Secret Store -- 시크릿카드 Phase0 저장소.

설계서 ``_dsbot_ref/design/03_secrets_design.md`` §5 Phase0 항목 (3).
ds-bot ``secrets.js`` 의 저장 포맷/검증/요청 상태머신을
``plugin_credentials.py`` 의 경로 규칙·원자적 쓰기 패턴 위에 얹은
다온판 이식이다.

보안 규칙 (값 로그 금지):
  - 이 모듈의 어떤 로그 메시지도 시크릿 **값** 을 포함하지 않는다.
    로그에는 이름(name)·요청 id 등 식별자만 기록한다.
  - 목록/조회 공개 API(``list_secrets``/``secretView``/요청 조회)는
    값 필드를 절대 포함하지 않는다. 값 반환은 셸 환경변수 주입용
    내부 전용 함수(``get_secret_value``)로만 제공한다.

저장 위치 (``plugin_credentials._resolve_credentials_dir`` 과 동일 규칙):
  - PyInstaller(배포):  %LOCALAPPDATA%/Daon/daon_secrets.json
  - 개발 모드:          <repo>/data/daon_secrets.json

파일 스키마::

    { "version": 1,
      "secrets": [ { name, value, purpose, scope, requestedBy,
                     createdAt, updatedAt, lastUsedAt? }, ... ],
      "requests": { "<botOrSessionId>": [ request, ... ] } }

``request``::

    { id, name, purpose, kind: 'fill'|'allow', status,
      askedAt, settledAt?, scope? }

쓰기는 tmp 파일 후 rename(원자적) + chmod 600 이다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    from .value_redactor import MAX_VALUE, MIN_VALUE, SECRET_NAME_RE, normalizeName
except ImportError:  # 서버가 api/ 를 sys.path에 올린 경우(api.value_redactor)
    try:
        from api.value_redactor import MAX_VALUE, MIN_VALUE, SECRET_NAME_RE, normalizeName
    except ImportError:  # 단독 스크립트 실행 등 최후 폴백
        from value_redactor import MAX_VALUE, MIN_VALUE, SECRET_NAME_RE, normalizeName  # type: ignore

_logger = logging.getLogger(__name__)

_lock = threading.RLock()

STORE_VERSION = 1
STORE_FILENAME = "daon_secrets.json"

MAX_REQUESTS = 20
MAX_PURPOSE = 300

_REQUEST_KINDS = ("fill", "allow")
_REQUEST_STATUSES = ("pending", "saved", "allowed", "cancelled")

Scope = Union[str, List[str]]


# ---------------------------------------------------------------------------
# 경로 결정 (plugin_credentials.py:35-47 과 동일 규칙 — Phase0는 공용화 없이 복제)
# ---------------------------------------------------------------------------

def _resolve_secrets_dir() -> Path:
    """PyInstaller 배포 시 %LOCALAPPDATA%/Daon, 개발 시 <repo>/data."""
    try:
        if getattr(__import__("sys"), "frozen", False):
            local = os.environ.get("LOCALAPPDATA") or str(Path.home())
            return Path(local) / "Daon"
    except Exception:  # pragma: no cover - 방어적
        pass
    return Path(__file__).resolve().parent.parent / "data"


def _secrets_file() -> Path:
    return _resolve_secrets_dir() / STORE_FILENAME


# ---------------------------------------------------------------------------
# 내부 저장/로드 (원자적 쓰기 + chmod 600 — plugin_credentials._save 패턴)
# ---------------------------------------------------------------------------

def _empty_store() -> dict:
    return {"version": STORE_VERSION, "secrets": [], "requests": {}}


def _load() -> dict:
    """전체 store 를 로드한다. 값은 이 모듈 내부에서만 다룬다."""
    with _lock:
        try:
            path = _secrets_file()
            if path.exists():
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    return _sanitize(data)
        except Exception as exc:
            # 값 로그 금지 — 에러 코드/이름만 기록 (secrets.js:97-98 reason 패턴)
            _logger.warning(
                "Failed to load secrets file: %s",
                getattr(exc, "errno", None) or type(exc).__name__,
            )
        return _empty_store()


def _save(data: dict) -> None:
    with _lock:
        try:
            path = _secrets_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
                try:
                    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
                except Exception:
                    pass
                tmp.replace(path)
                try:
                    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
                except Exception:
                    pass
            except Exception:
                try:
                    tmp.unlink()
                except Exception:
                    pass
                raise
        except Exception as exc:
            # 값 로그 금지 — 에러 종류만 기록
            _logger.error("Failed to save secrets file: %s", type(exc).__name__)


def _sanitize(data: dict) -> dict:
    """로드 시 검증 — 이름 규칙·값 타입이 어긋난 항목은 skip (secrets.js:113-125)."""
    secrets: List[dict] = []
    for entry in data.get("secrets") if isinstance(data.get("secrets"), list) else []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        value = entry.get("value")
        if not isinstance(name, str) or not SECRET_NAME_RE.match(name):
            continue
        if not isinstance(value, str):
            continue
        secrets.append({
            "name": name,
            "value": value,
            "purpose": entry.get("purpose") if isinstance(entry.get("purpose"), str) else "",
            "scope": _clean_scope_loaded(entry.get("scope")),
            "requestedBy": entry.get("requestedBy") if isinstance(entry.get("requestedBy"), str) else None,
            "createdAt": _as_epoch(entry.get("createdAt")),
            "updatedAt": _as_epoch(entry.get("updatedAt")),
            **({"lastUsedAt": entry["lastUsedAt"]} if _is_finite_number(entry.get("lastUsedAt")) else {}),
        })
    requests: Dict[str, list] = {}
    raw_requests = data.get("requests")
    if isinstance(raw_requests, dict):
        for owner, items in raw_requests.items():
            if not isinstance(owner, str) or not isinstance(items, list):
                continue
            cleaned = [_clean_request(item) for item in items]
            requests[owner] = [item for item in cleaned if item is not None]
    return {"version": STORE_VERSION, "secrets": secrets, "requests": requests}


def _as_epoch(raw: Any) -> float:
    try:
        epoch = float(raw)
    except (TypeError, ValueError):
        return time.time()
    return epoch


def _is_finite_number(raw: Any) -> bool:
    return isinstance(raw, (int, float)) and raw == raw and raw not in (float("inf"), float("-inf"))


def _clean_scope_loaded(raw: Any) -> Scope:
    if raw == "all":
        return "all"
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, str)]
    return "all"


def _clean_request(item: Any) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    request_id = item.get("id")
    name = item.get("name")
    if not isinstance(request_id, str) or not request_id:
        return None
    if not isinstance(name, str) or not SECRET_NAME_RE.match(name):
        return None
    kind = item.get("kind")
    status = item.get("status")
    return {
        "id": request_id,
        "name": name,
        "purpose": item.get("purpose") if isinstance(item.get("purpose"), str) else "",
        "kind": kind if kind in _REQUEST_KINDS else "fill",
        "status": status if status in _REQUEST_STATUSES else "pending",
        "askedAt": _as_epoch(item.get("askedAt")),
        **({"settledAt": item["settledAt"]} if _is_finite_number(item.get("settledAt")) else {}),
        **({"scope": item["scope"]} if item.get("scope") in ("all",) or isinstance(item.get("scope"), list) else {}),
    }


# ---------------------------------------------------------------------------
# 검증 헬퍼 (secrets.js:23-28, 228-239)
# ---------------------------------------------------------------------------

def canUse(secret: Optional[dict], bot_id: Optional[str]) -> bool:  # noqa: N802 - 원본명 이식
    """``scope==='all'`` 또는 scope 배열에 ``bot_id`` 포함이면 사용 가능."""
    if secret is None or bot_id is None:
        return False
    scope = secret.get("scope")
    if scope == "all":
        return True
    return isinstance(scope, list) and bot_id in scope


# snake_case 별칭
can_use = canUse


def clean_value(raw: Any) -> str:
    """값 검증 — 앞뒤 공백 제거, MIN/MAX 길이 강제 (secrets.js:228-234)."""
    if not isinstance(raw, str):
        raise ValueError("Type the key first")
    value = raw.strip()
    if len(value) < MIN_VALUE:
        raise ValueError(f"A key must have at least {MIN_VALUE} characters")
    if len(value) > MAX_VALUE:
        raise ValueError(f"A key can have at most {MAX_VALUE} characters")
    return value


def clean_scope(raw: Any) -> Scope:
    """scope 검증 — ``'all'`` 또는 문자열 id 목록 (secrets.js:235-239)."""
    if raw == "all" or raw is None:
        return "all"
    if isinstance(raw, list):
        seen: List[str] = []
        for item in raw:
            if isinstance(item, str) and item and item not in seen:
                seen.append(item)
        return seen
    raise ValueError("Choose every Bot or a list of Bots")


def secretView(secret: dict) -> dict:  # noqa: N802 - 원본명 이식
    """값 없는 공개 뷰 (secrets.js:247-255) — value 필드 절대 미포함."""
    view = {
        "name": secret.get("name"),
        "purpose": secret.get("purpose", ""),
        "scope": secret.get("scope", "all"),
        "requestedBy": secret.get("requestedBy"),
        "createdAt": secret.get("createdAt"),
        "updatedAt": secret.get("updatedAt"),
    }
    if secret.get("lastUsedAt") is not None:
        view["lastUsedAt"] = secret.get("lastUsedAt")
    return view


# snake_case 별칭
secret_view = secretView


# ---------------------------------------------------------------------------
# Secret CRUD
# ---------------------------------------------------------------------------

def _find_secret(data: dict, name: str) -> Optional[dict]:
    for entry in data.get("secrets", []):
        if entry.get("name") == name:
            return entry
    return None


def set_secret(
    name: str,
    value: str,
    purpose: str = "",
    scope: Scope = "all",
    requested_by: Optional[str] = None,
) -> dict:
    """시크릿 저장(신규/갱신). 사용자(UI secure input)만 호출해야 한다.

    이름은 ``normalizeName`` 으로 정규화되며 ``SECRET_NAME_RE`` 를
    만족해야 한다. 반환값은 값 없는 뷰이다.
    """
    norm = normalizeName(name)
    if not SECRET_NAME_RE.match(norm):
        raise ValueError(f"Invalid secret name: {norm}")
    clean = clean_value(value)
    checked_scope = clean_scope(scope)
    purpose_text = (purpose or "")[:MAX_PURPOSE]
    now = time.time()
    with _lock:
        data = _load()
        existing = _find_secret(data, norm)
        entry = {
            "name": norm,
            "value": clean,
            "purpose": purpose_text if purpose_text else (existing.get("purpose", "") if existing else ""),
            "scope": checked_scope,
            "requestedBy": requested_by if requested_by is not None else (existing.get("requestedBy") if existing else None),
            "createdAt": existing.get("createdAt", now) if existing else now,
            "updatedAt": now,
            **({"lastUsedAt": existing["lastUsedAt"]} if existing and existing.get("lastUsedAt") is not None else {}),
        }
        if existing is None:
            data["secrets"].append(entry)
        else:
            data["secrets"][data["secrets"].index(existing)] = entry
        _reconcile_locked(data, norm)
        _save(data)
        _logger.info("Secret saved: %s", norm)
        return secretView(entry)


def get_secret_value(name: str) -> Optional[str]:
    """내부 전용 — 셸 환경변수 주입 시에만 사용. 값을 로그하지 말 것."""
    norm = normalizeName(name)
    with _lock:
        data = _load()
        entry = _find_secret(data, norm)
        return entry.get("value") if entry else None


def has_secret(name: str) -> bool:
    with _lock:
        data = _load()
        return _find_secret(data, normalizeName(name)) is not None


def get_secret(name: str) -> Optional[dict]:
    """값 없는 공개 뷰 또는 None."""
    with _lock:
        data = _load()
        entry = _find_secret(data, normalizeName(name))
        return secretView(entry) if entry else None


def list_secrets() -> List[dict]:
    """이름순 값 없는 뷰 목록."""
    with _lock:
        data = _load()
        return [secretView(entry) for entry in sorted(data.get("secrets", []), key=lambda e: e.get("name", ""))]


def update_scope(name: str, scope: Scope) -> Optional[dict]:
    """scope 만 변경 (``secret-update`` 카드 대응)."""
    norm = normalizeName(name)
    checked = clean_scope(scope)
    now = time.time()
    with _lock:
        data = _load()
        entry = _find_secret(data, norm)
        if entry is None:
            return None
        entry["scope"] = checked
        entry["updatedAt"] = now
        _reconcile_locked(data, norm)
        _save(data)
        _logger.info("Secret scope updated: %s", norm)
        return secretView(entry)


def allow_secret(name: str, bot_id: str) -> Optional[dict]:
    """저장된 시크릿에 bot 단독 scope 추가 (``secret-allow`` 카드 대응)."""
    norm = normalizeName(name)
    now = time.time()
    with _lock:
        data = _load()
        entry = _find_secret(data, norm)
        if entry is None:
            return None
        if entry.get("scope") != "all":
            scope = list(entry.get("scope") or [])
            if bot_id not in scope:
                scope.append(bot_id)
            entry["scope"] = scope
            entry["updatedAt"] = now
        _reconcile_locked(data, norm)
        _save(data)
        _logger.info("Secret allowed: %s", norm)
        return secretView(entry)


def delete_secret(name: str) -> bool:
    norm = normalizeName(name)
    with _lock:
        data = _load()
        entry = _find_secret(data, norm)
        if entry is None:
            return False
        data["secrets"].remove(entry)
        _save(data)
        _logger.info("Secret deleted: %s", norm)
        return True


def note_used(name: str) -> None:
    """``lastUsedAt`` 갱신 — 호출자가 디바운스/지연 저장을 담당 (Phase0는 즉시 저장)."""
    norm = normalizeName(name)
    with _lock:
        data = _load()
        entry = _find_secret(data, norm)
        if entry is None:
            return
        entry["lastUsedAt"] = time.time()
        _save(data)


def entries_for_redactor(bot_id: Optional[str] = None) -> List[dict]:
    """``value_redactor.redactor`` 입력용 ``{name, value}`` 목록 (내부 전용).

    ``bot_id`` 가 주어지면 ``canUse`` 인 항목만 포함한다.
    반환된 값들을 로그/응답에 절대 싣지 말 것.
    """
    with _lock:
        data = _load()
        items = data.get("secrets", [])
        if bot_id is not None:
            items = [entry for entry in items if canUse(entry, bot_id)]
        return [{"name": entry["name"], "value": entry["value"]} for entry in items]


def daon_secret_env(session_id: Optional[str] = None) -> Dict[str, str]:
    """셸 환경변수 주입용 ``{DAON_SECRET_<NAME>: value}`` (내부 전용).

    ``plugin_gateway.session_plugin_credentials`` 와 동형. ``canUse``
    (scope ``all`` 또는 세션 포함)인 항목만 포함한다. 값은 이 dict
    안에서만 다루고 로그/응답에 절대 싣지 말 것.
    """
    try:
        from api.value_redactor import env_name  # 서버 sys.path (api/ 우선)
    except ImportError:
        try:
            from .value_redactor import env_name  # 패키지 상대
        except ImportError:
            from value_redactor import env_name  # type: ignore
    with _lock:
        data = _load()
        out: Dict[str, str] = {}
        for entry in data.get("secrets", []) or []:
            try:
                if session_id is not None and not canUse(entry, session_id):
                    continue
                name = entry.get("name")
                value = entry.get("value")
                if not name or not isinstance(value, str) or not value:
                    continue
                out[env_name(name)] = value
            except Exception:
                continue
        return out


# ---------------------------------------------------------------------------
# Pending requests (secrets.js:166-179, 198-219)
# ---------------------------------------------------------------------------

def _requests_of(data: dict, owner: str) -> list:
    return data.setdefault("requests", {}).setdefault(owner, [])


def _find_request(data: dict, request_id: str) -> Optional[tuple]:
    for owner, items in (data.get("requests") or {}).items():
        for request in items:
            if request.get("id") == request_id:
                return owner, request
    return None


def _trim_requests(items: list) -> None:
    while len(items) > MAX_REQUESTS:
        settled = next((i for i, r in enumerate(items) if r.get("status") != "pending"), None)
        items.pop(settled if settled is not None else 0)


def _settle_locked(request: dict, status: str, scope: Scope = "all") -> None:
    request["status"] = status
    request["settledAt"] = time.time()
    if status in ("saved", "allowed"):
        request["scope"] = scope


def _reconcile_locked(data: dict, name: str) -> None:
    """이름 변경 후 대기중 요청들을 자동 해소/재판정 (secrets.js:206-219)."""
    secret = _find_secret(data, name)
    for owner, items in (data.get("requests") or {}).items():
        for request in items:
            if request.get("status") != "pending" or request.get("name") != name:
                continue
            if canUse(secret, owner):
                _settle_locked(request, "saved", secret.get("scope", "all") if secret else "all")
            else:
                request["kind"] = "allow" if secret else "fill"


def add_request(
    owner: str,
    name: str,
    purpose: str = "",
    session_id: str = "",
) -> Optional[dict]:
    """Agent 가 시크릿을 요청한 것을 기록한다 (``request_secret`` 대응).

    이미 ``canUse`` 이면 기록하지 않고 None 반환. ``kind`` 는 기존
    시크릿이 있으면 ``'allow'``(권한만), 없으면 ``'fill'``(값 입력).
    값은 기록하지 않고 메타데이터만 기록하므로 안전하다.
    """
    if not owner:
        raise ValueError("owner is required")
    norm = normalizeName(name)
    if not SECRET_NAME_RE.match(norm):
        raise ValueError(f"Invalid secret name: {norm}")
    purpose_text = re.sub(r"\s+", " ", (purpose or "").strip())[:MAX_PURPOSE]
    with _lock:
        data = _load()
        if canUse(_find_secret(data, norm), owner):
            return None
        items = _requests_of(data, owner)
        for request in items:
            if request.get("status") == "pending" and request.get("name") == norm:
                return dict(request)
        request = {
            "id": uuid.uuid4().hex,
            "name": norm,
            "purpose": purpose_text,
            "kind": "allow" if _find_secret(data, norm) else "fill",
            "status": "pending",
            "askedAt": time.time(),
            **({"sessionId": session_id} if session_id else {}),
        }
        items.append(request)
        _trim_requests(items)
        _save(data)
        _logger.info("Secret requested: %s (kind=%s)", norm, request["kind"])
        return dict(request)


def list_requests(owner: Optional[str] = None, pending_only: bool = False) -> List[dict]:
    """요청 목록 — 값 필드 없음."""
    with _lock:
        data = _load()
        owners = [owner] if owner is not None else sorted((data.get("requests") or {}).keys())
        result = []
        for key in owners:
            for request in (data.get("requests") or {}).get(key, []):
                if pending_only and request.get("status") != "pending":
                    continue
                result.append({"owner": key, **request})
        return result


def get_request(request_id: str) -> Optional[dict]:
    with _lock:
        data = _load()
        found = _find_request(data, request_id)
        if found is None:
            return None
        owner, request = found
        return {"owner": owner, **request}


def _settle_request(request_id: str, status: str) -> Optional[dict]:
    with _lock:
        data = _load()
        found = _find_request(data, request_id)
        if found is None:
            return None
        owner, request = found
        if request.get("status") != "pending":
            raise ValueError("This request was already answered")
        secret = _find_secret(data, request.get("name", ""))
        _settle_locked(request, status, secret.get("scope", "all") if secret else "all")
        _save(data)
        _logger.info("Secret request %s: %s", status, request.get("name"))
        return {"owner": owner, **request}


def cancel_request(request_id: str) -> Optional[dict]:
    """요청 취소 (``secret-cancel`` 카드 대응)."""
    return _settle_request(request_id, "cancelled")


def forget_owner(owner: str) -> None:
    """봇/세션 삭제 시 정리 — scope 에서 제거 + 요청 삭제."""
    if not owner:
        return
    with _lock:
        data = _load()
        now = time.time()
        for entry in data.get("secrets", []):
            scope = entry.get("scope")
            if isinstance(scope, list) and owner in scope:
                entry["scope"] = [item for item in scope if item != owner]
                entry["updatedAt"] = now
        (data.get("requests") or {}).pop(owner, None)
        _save(data)
        _logger.info("Secrets forgotten for owner")
