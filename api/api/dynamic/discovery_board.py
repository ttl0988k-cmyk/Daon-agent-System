"""
시나리오 D — Dynamic Harness Discovery Layer: DiscoveryBoard.

DAG(ParallelRunner)는 노드 사이의 "명시적 데이터 흐름"을 담당한다.
업스트림 노드가 끝나야 선언된 엣지를 통해서만 정보가 내려간다.
DiscoveryBoard는 여기에 두 번째 평면, 즉 "비동기 발견 공유" 계층을 추가한다.
에이전트는 작업 도중 중요한 발견을 게시하고, 다른 에이전트는 자기 작업을
멈추지 않고도 그 발견을 인지할 수 있다 (수동적 인지, passive awareness).

설계 불변식:
- Discovery는 DAG를 대체하지 않는다. DAG = 작업 의존성, Board = 발견 공유.
- 보드는 미션(run) 단위 격리. HermesDynamicRunner.run()이 하나를 만들어
  mission_tracker["discovery_board"]에 싣고, ParallelRunner가 읽어
  노드 워커 스레드에 thread-local로 노출한다.
- 노이즈 방어: 에이전트당 publish 상한, 요약 해시 기반 중복 병합,
  digest에서 LOW 폐기, summary/evidence 길이 상한.
- 모든 공개 메서드는 스레드 안전하며 절대 raise하지 않는다.
  보드 장애가 하네스 실행을 깨뜨리면 안 된다.
"""

import hashlib
import threading
import time
from dataclasses import dataclass, field, asdict

from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)

# ── 와이어 상수 (프론트/프로브가 의존하는 문자열) ──
IMPORTANCE_HIGH = "high"
IMPORTANCE_MEDIUM = "medium"
IMPORTANCE_LOW = "low"
_IMPORTANCE_ORDER = {IMPORTANCE_HIGH: 3, IMPORTANCE_MEDIUM: 2, IMPORTANCE_LOW: 1}

DISCOVERY_TOOLSET = "discovery"
TOOL_BROADCAST_DISCOVERY = "broadcast_discovery"
TOOL_CHECK_TEAM_DISCOVERIES = "check_team_discoveries"
DISCOVERY_TOOL_NAMES = (TOOL_BROADCAST_DISCOVERY, TOOL_CHECK_TEAM_DISCOVERIES)

# limits["discovery"] 기본값 (limits.py의 default_limits와 동일하게 유지)
DEFAULT_DISCOVERY_LIMITS = {
    "enabled": True,
    "max_publish_per_agent": 5,
    "max_total": 40,
    "digest_max_items": 8,
    "max_summary_chars": 400,
    "max_evidence_chars": 600,
}


@dataclass
class Discovery:
    """게시된 발견 1건. JSON 직렬화 가능."""
    run_id: str
    source_agent: str
    summary: str
    importance: str = IMPORTANCE_MEDIUM
    confidence: float = 0.5
    type: str = "finding"
    evidence: str = ""
    affected_tasks: list = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    count: int = 1  # 중복 게시 병합 횟수

    def to_dict(self) -> dict:
        return asdict(self)


def _norm_summary_key(summary: str) -> str:
    """중복 판정용 정규화 키: 소문자 + 공백 접힘."""
    return " ".join(str(summary or "").lower().split())


def _tokens(text: str) -> set:
    """관련도 판정용 토큰 집합 (capability_resolver._tokens와 동일 휴리스틱)."""
    import re as _re
    try:
        return {t for t in _re.findall(r"[a-z0-9_]+", str(text or "").lower()) if len(t) >= 3}
    except Exception:
        return set()


class DiscoveryBoard:
    """run 단위 격리 + 스레드 안전 발견 게시판.

    공개 메서드는 절대 raise하지 않는다. 실패는 (False, reason) 또는
    빈 결과로 열화되어 하네스 실행에 영향을 주지 않는다.
    """

    def __init__(self, run_id: str, limits: dict = None):
        self.run_id = str(run_id or "unknown_run")
        cfg = dict(DEFAULT_DISCOVERY_LIMITS)
        try:
            if isinstance(limits, dict) and isinstance(limits.get("discovery"), dict):
                cfg.update(limits["discovery"])
        except Exception:
            pass
        self.enabled = bool(cfg.get("enabled", True))
        self.max_publish_per_agent = int(cfg.get("max_publish_per_agent", 5))
        self.max_total = int(cfg.get("max_total", 40))
        self.digest_max_items = int(cfg.get("digest_max_items", 8))
        self.max_summary_chars = int(cfg.get("max_summary_chars", 400))
        self.max_evidence_chars = int(cfg.get("max_evidence_chars", 600))
        self._lock = threading.RLock()
        self._items: list = []
        self._by_hash: dict = {}
        self._publish_counts: dict = {}
        self._subscribers: dict = {}

    # ------------------------------------------------------------------
    # publish / subscribe
    # ------------------------------------------------------------------

    def publish(self, source_agent: str, summary: str, importance: str = IMPORTANCE_MEDIUM,
                confidence=0.5, dtype: str = "finding", evidence: str = "",
                affected_tasks=None) -> tuple:
        """발견 1건을 게시한다. 반환: (ok, reason).

        reason: published | merged | publish_limit | board_full |
                empty_summary | disabled | error
        """
        try:
            if not self.enabled:
                return False, "disabled"
            agent = str(source_agent or "").strip() or "unknown"
            text = str(summary or "").strip()
            if not text:
                return False, "empty_summary"
            text = text[: self.max_summary_chars]
            imp = str(importance or IMPORTANCE_MEDIUM).strip().lower()
            if imp not in _IMPORTANCE_ORDER:
                imp = IMPORTANCE_MEDIUM
            try:
                conf = float(confidence)
            except (TypeError, ValueError):
                conf = 0.5
            conf = max(0.0, min(1.0, conf))
            ev = str(evidence or "").strip()[: self.max_evidence_chars]
            tasks = []
            try:
                for t in (affected_tasks or []):
                    t2 = str(t or "").strip()
                    if t2 and t2 not in tasks:
                        tasks.append(t2)
            except TypeError:
                tasks = []

            notify_targets = []
            with self._lock:
                key = hashlib.sha1(_norm_summary_key(text).encode("utf-8")).hexdigest()
                existing = self._by_hash.get(key)
                if existing is not None:
                    # 중복 발견: 병합 (confidence 가산, 중요도 승격, 횟수 증가).
                    # 병합은 quota를 소비하지 않는다. 같은 발견의 재확인은 노이즈가
                    # 아니라 팀의 합의 신호이므로 상한과 무관하게 허용한다.
                    existing.count += 1
                    existing.confidence = max(existing.confidence, conf)
                    if _IMPORTANCE_ORDER.get(imp, 0) > _IMPORTANCE_ORDER.get(existing.importance, 0):
                        existing.importance = imp
                    if ev and not existing.evidence:
                        existing.evidence = ev
                    for t in tasks:
                        if t not in existing.affected_tasks:
                            existing.affected_tasks.append(t)
                    existing.timestamp = time.time()
                    notify_targets = list(existing.affected_tasks)
                    result = (True, "merged")
                else:
                    # 신규 발견만 quota를 소비한다.
                    used = self._publish_counts.get(agent, 0)
                    if used >= self.max_publish_per_agent:
                        return False, "publish_limit"
                    if len(self._items) >= self.max_total:
                        # 자리 확보: 가장 오래된 LOW부터 퇴거.
                        # _by_hash 키는 SHA-1 해시이므로 동일 방식으로 삭제해야
                        # 유령 엔트리가 남지 않는다.
                        evicted = False
                        for i, d in enumerate(self._items):
                            if d.importance == IMPORTANCE_LOW:
                                _evict_key = hashlib.sha1(
                                    _norm_summary_key(d.summary).encode("utf-8")
                                ).hexdigest()
                                self._by_hash.pop(_evict_key, None)
                                del self._items[i]
                                evicted = True
                                break
                        if not evicted:
                            return False, "board_full"
                    disc = Discovery(
                        run_id=self.run_id,
                        source_agent=agent,
                        summary=text,
                        importance=imp,
                        confidence=conf,
                        type=str(dtype or "finding").strip() or "finding",
                        evidence=ev,
                        affected_tasks=tasks,
                    )
                    self._items.append(disc)
                    self._by_hash[key] = disc
                    self._publish_counts[agent] = used + 1
                    notify_targets = list(tasks)
                    result = (True, "published")
            # 구독자 알림 (락 밖에서, 절대 전파 금지)
            self._notify(agent, notify_targets)
            _log.info("[DiscoveryBoard] %s '%s' published by %s (%s)",
                      result[1], text[:80], agent, imp)
            return result
        except Exception as e:
            _log.debug("DiscoveryBoard.publish failed: %s", e)
            return False, "error"

    def subscribe(self, agent_name: str, callback=None) -> bool:
        """2단계 passive injection을 위한 구독 등록. 절대 raise하지 않는다."""
        try:
            name = str(agent_name or "").strip()
            if not name:
                return False
            with self._lock:
                subs = self._subscribers.setdefault(name, [])
                if callback is not None and callback not in subs:
                    subs.append(callback)
            return True
        except Exception:
            return False

    def _notify(self, publisher: str, affected: list) -> None:
        callbacks = []
        try:
            with self._lock:
                if affected:
                    names = [a for a in affected if a in self._subscribers and a != publisher]
                else:
                    names = [n for n in self._subscribers if n != publisher]
                for n in names:
                    callbacks.extend(self._subscribers.get(n, []))
        except Exception:
            return
        for cb in callbacks:
            try:
                cb(publisher)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # read
    # ------------------------------------------------------------------

    def recent(self, limit: int = 20, since: float = None, exclude_agent: str = None,
               min_importance: str = None) -> list:
        """최신순 발견 목록. 절대 raise하지 않는다."""
        try:
            min_rank = _IMPORTANCE_ORDER.get(str(min_importance or "").lower(), 0)
            with self._lock:
                items = list(self._items)
            out = []
            for d in sorted(items, key=lambda x: x.timestamp, reverse=True):
                if since is not None and d.timestamp < since:
                    continue
                if exclude_agent and d.source_agent == exclude_agent:
                    continue
                if min_rank and _IMPORTANCE_ORDER.get(d.importance, 0) < min_rank:
                    continue
                out.append(d)
                if len(out) >= max(1, int(limit or 20)):
                    break
            return out
        except Exception:
            return []

    def relevance_filter(self, agent_name: str, subtask: str = "") -> list:
        """에이전트 관련 발견 필터.

        규칙: (1) affected_tasks에 내 이름 포함 → 관련
              (2) HIGH는 항상 관련 (전 팀 즉시 인지)
              (3) 내 subtask와 토큰 겹침 2개 이상 → 관련
        """
        try:
            name = str(agent_name or "").strip()
            my_tokens = _tokens(subtask) | _tokens(name)
            with self._lock:
                items = list(self._items)
            out = []
            for d in items:
                if name and name in d.affected_tasks:
                    out.append(d)
                    continue
                if d.importance == IMPORTANCE_HIGH:
                    out.append(d)
                    continue
                if my_tokens and len(my_tokens & (_tokens(d.summary) | _tokens(" ".join(d.affected_tasks)))) >= 2:
                    out.append(d)
            return out
        except Exception:
            return []

    def compress(self, agent_name: str = None, subtask: str = "",
                 min_importance: str = IMPORTANCE_MEDIUM, max_items: int = None) -> str:
        """주입용 digest 텍스트. LOW는 기본 폐기. 비어있으면 '' 반환."""
        try:
            if not self.enabled:
                return ""
            if agent_name:
                pool = self.relevance_filter(agent_name, subtask)
                pool = [d for d in pool if d.source_agent != agent_name]
            else:
                with self._lock:
                    pool = list(self._items)
            min_rank = _IMPORTANCE_ORDER.get(str(min_importance or IMPORTANCE_MEDIUM).lower(), 2)
            pool = [d for d in pool if _IMPORTANCE_ORDER.get(d.importance, 0) >= min_rank]
            if not pool:
                return ""
            pool.sort(key=lambda d: (_IMPORTANCE_ORDER.get(d.importance, 0), d.timestamp), reverse=True)
            cap = int(max_items or self.digest_max_items)
            pool = pool[:cap]
            lines = ["[TEAM DISCOVERY BOARD] Findings shared by other agents during this run:"]
            for d in pool:
                line = f"- [{d.importance.upper()}] ({d.source_agent}, conf {d.confidence:.2f}): {d.summary}"
                if d.evidence:
                    line += f" [evidence: {d.evidence[:200]}]"
                if d.affected_tasks:
                    line += f" [affects: {', '.join(d.affected_tasks[:5])}]"
                lines.append(line)
            lines.append("Use check_team_discoveries for details; use broadcast_discovery to share your own key findings.")
            digest = "\n".join(lines)
            try:
                from api.dynamic.dag_utils import _compress_context
                digest = _compress_context(digest)
            except Exception:
                pass
            return digest
        except Exception as e:
            _log.debug("DiscoveryBoard.compress failed: %s", e)
            return ""

    def stats(self) -> dict:
        try:
            with self._lock:
                by_imp = {}
                for d in self._items:
                    by_imp[d.importance] = by_imp.get(d.importance, 0) + 1
                return {
                    "run_id": self.run_id,
                    "enabled": self.enabled,
                    "total": len(self._items),
                    "by_importance": by_imp,
                    "publish_counts": dict(self._publish_counts),
                }
        except Exception:
            return {"run_id": self.run_id, "total": 0}


# ----------------------------------------------------------------------
# thread-local 노출 (delegation의 set_current_delegation 패턴 재사용)
# ----------------------------------------------------------------------

_board_state = threading.local()


def set_current_board(board, agent_name: str = None) -> None:
    """노드 워커 스레드에 보드 + 호출자 이름을 노출한다."""
    _board_state.board = board
    _board_state.agent = agent_name


def get_current_board():
    return getattr(_board_state, "board", None)


def get_current_agent() -> str:
    return getattr(_board_state, "agent", None) or ""


def clear_current_board() -> None:
    _board_state.board = None
    _board_state.agent = None


# ----------------------------------------------------------------------
# 도구 핸들러 + registry 등록
# ----------------------------------------------------------------------

def _handle_broadcast_discovery(args, **kwargs) -> str:
    import json as _json
    try:
        board = get_current_board()
        agent = get_current_agent() or "unknown"
        if board is None:
            return _json.dumps({"ok": False, "reason": "no_board"})
        if not isinstance(args, dict):
            args = {}
        ok, reason = board.publish(
            source_agent=agent,
            summary=str(args.get("summary") or ""),
            importance=str(args.get("importance") or IMPORTANCE_MEDIUM),
            confidence=args.get("confidence", 0.5),
            dtype=str(args.get("type") or "finding"),
            evidence=str(args.get("evidence") or ""),
            affected_tasks=args.get("affected_tasks") or [],
        )
        return _json.dumps({"ok": ok, "reason": reason}, ensure_ascii=False)
    except Exception as e:
        return _json.dumps({"ok": False, "reason": f"error: {e}"})


def _handle_check_team_discoveries(args, **kwargs) -> str:
    import json as _json
    try:
        board = get_current_board()
        agent = get_current_agent() or ""
        if board is None:
            return _json.dumps({"discoveries": [], "reason": "no_board"})
        if not isinstance(args, dict):
            args = {}
        min_imp = str(args.get("min_importance") or IMPORTANCE_LOW)
        try:
            limit = int(args.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        items = board.recent(limit=limit, exclude_agent=agent, min_importance=min_imp)
        return _json.dumps({"discoveries": [d.to_dict() for d in items]},
                           ensure_ascii=False, default=str)
    except Exception as e:
        return _json.dumps({"discoveries": [], "reason": f"error: {e}"})


BROADCAST_SCHEMA = {
    "name": TOOL_BROADCAST_DISCOVERY,
    "description": (
        "Publish an important finding to the team DiscoveryBoard so other agents "
        "working in parallel can see it without waiting for your node to finish. "
        "Use ONLY for findings that change how teammates should work: blockers, "
        "wrong assumptions, key facts, or missing capabilities. Do NOT publish "
        "routine progress. Publish budget is limited per agent."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One-sentence finding (required)"},
            "importance": {"type": "string", "enum": ["high", "medium", "low"],
                           "description": "high = teammates must know now; medium = useful context; low = minor note"},
            "confidence": {"type": "number", "description": "0.0-1.0 how sure you are"},
            "type": {"type": "string", "description": "finding | blocker | capability_gap | correction"},
            "evidence": {"type": "string", "description": "File path, command output snippet, or short proof"},
            "affected_tasks": {"type": "array", "items": {"type": "string"},
                               "description": "Names of nodes/agents this finding affects"},
        },
        "required": ["summary"],
    },
}

CHECK_SCHEMA = {
    "name": TOOL_CHECK_TEAM_DISCOVERIES,
    "description": (
        "Read current team discoveries from the DiscoveryBoard (excluding your own). "
        "Call when you are stuck, before a risky step, or when you suspect another "
        "agent may have found information relevant to your subtask."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "min_importance": {"type": "string", "enum": ["high", "medium", "low"]},
            "limit": {"type": "integer", "description": "Max number of discoveries to return (default 10)"},
        },
    },
}


def register_discovery_tools(registry) -> bool:
    """broadcast/check 도구를 registry에 멱등 등록. 등록했으면 True."""
    try:
        existing = set(registry.get_all_tool_names())
        registered = False
        if TOOL_BROADCAST_DISCOVERY not in existing:
            registry.register(
                name=TOOL_BROADCAST_DISCOVERY,
                toolset=DISCOVERY_TOOLSET,
                schema=BROADCAST_SCHEMA,
                handler=_handle_broadcast_discovery,
                is_async=False,
                description=BROADCAST_SCHEMA["description"],
            )
            registered = True
        if TOOL_CHECK_TEAM_DISCOVERIES not in existing:
            registry.register(
                name=TOOL_CHECK_TEAM_DISCOVERIES,
                toolset=DISCOVERY_TOOLSET,
                schema=CHECK_SCHEMA,
                handler=_handle_check_team_discoveries,
                is_async=False,
                description=CHECK_SCHEMA["description"],
            )
            registered = True
        return registered
    except Exception as e:
        _log.debug("register_discovery_tools failed: %s", e)
        return False
