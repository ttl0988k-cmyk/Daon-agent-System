"""LiveBench Source — 라이브 벤치마크 소스 레이어.

LiveBench 1.0 / 2.0 리더보드를 내려받아 캐시하고, 우리 등록 모델명에
매칭해 분야별 실측 점수를 제공한다.

설계 (2026-10-15 대표님 지시):
  - 벤치마크 수치를 '손으로 채우지 않는다'. LiveBench가 릴리스마다
    공개하는 CSV를 소스로 삼아 자동으로 따라간다.
  - LiveBench 2.0 은 장기호라이즌 실작업 평가(decks, research reports,
    bug fixes, apps)로 우리 동적 하네스와 성격이 정확히 일치한다.
  - 분야별(design_layout/research/browser/coding/long_horizon/
    data_analysis/reasoning)로 모델 우위가 다르므로, 노드 역할에 맞는
    모델을 선별할 수 있다.

소스 엔드포인트 (livebench.ai JS 번들에서 리버스한 실제 경로):
  v1: https://livebench.ai/table_{rel}.csv            (rel = 2026_06_25)
      https://livebench.ai/categories_{rel}.json
  v2: https://livebench.ai/v2/table_{rel}.csv         (rel = 2026_09_02)
      https://livebench.ai/v2/categories_{rel}.json

캐시: ~/.hermes/livebench_cache.json (기본 24h TTL)
네트워크 실패 시 조용히 비활성화 — 선택기는 기존 경로로 계속 동작한다.
"""

import csv
import io
import json
import logging
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)

# ── 릴리스 ──────────────────────────────────────────────────────────────
# v1 파일명 = 날짜 그대로. v2 는 파일명이 'id'(2026-09-02)이고 라벨은
# '2026-10-14' 다(번들 Te 맵에서 확인). 캐시가 갱신되면 여기만 늘리면 된다.
_V1_BASE = "https://livebench.ai"
_V2_BASE = "https://livebench.ai/v2"
_V1_REL = "2026_06_25"     # v1 최종 게시 릴리스
_V2_REL = "2026_09_02"     # v2 최초 릴리스 (라벨 2026-10-14)
# v2 는 릴리스가 늘어나면 여기에 후보를 추가한다(앞에서부터 시도).
_V2_REL_CANDIDATES = ["2026_09_02"]

_CACHE_PATH = Path.home() / ".hermes" / "livebench_cache.json"
_CACHE_TTL_S = float(os.environ.get("DAON_LIVEBENCH_TTL", str(24 * 3600)))
_FETCH_TIMEOUT = 25
_UA = {"User-Agent": "Mozilla/5.0 (compatible; DAON-selector/1.0)"}

# ── LiveBench 분야 → 우리 capability 축 ────────────────────────────────
# 축 이름은 model_intel.CAPABILITIES 와 정렬한다:
#   coding, reasoning, debugging, agentic, tool_use, context
# v2 분야는 각각 고유 대응이 있고, v1 세부 카테고리는 더 잘게 매핑한다.
V2_FIELD_TO_CAPABILITY = {
    "coding":         ["coding"],
    # debugging 은 LiveBench 에 독립 축이 없다. 디버깅은 '코드에 대한 추론'이므로
    # reasoning 실측을 디버깅 축에도 반영한다. 이 매핑이 없으면 qa/reviewer/debug
    # 역할(strength→capability 'debugging')이 LiveBench 를 전혀 못 받아
    # 이름추정으로 퇴화한다(2026-10-15 실측: reviewer/debugger src=name).
    "reasoning":      ["reasoning", "debugging"],
    "long_horizon":   ["agentic", "context"],
    "research":       ["reasoning", "context"],
    "data_analysis":  ["coding", "reasoning", "debugging"],
    "design_layout":  ["coding"],
    "browser":        ["tool_use", "agentic"],
}

V1_CATEGORY_TO_CAPABILITY = {
    "Coding":          ["coding", "debugging"],
    "Agentic Coding":  ["coding", "agentic"],
    "Reasoning":       ["reasoning", "debugging"],
    "Mathematics":     ["reasoning"],
    "Data Analysis":   ["coding", "reasoning", "debugging"],
    "Language":        ["context"],
    "IF":              ["context"],
}

# ── 노드 역할 → LiveBench 분야 (가중치) ────────────────────────────────
# model_selector.ROLE_CAPABILITY_MAP 과 짝을 이룬다. 역할별로 어떤 분야를
# 주로 보는지 정의한다. 가중치 합은 1.0 이 아니어도 되며, 가중 평균한다.
ROLE_LIVEBENCH_AXES = {
    "developer":  {"coding": 0.6, "long_horizon": 0.4},
    "specialist": {"coding": 0.7, "long_horizon": 0.3},
    "coder":      {"coding": 0.8, "long_horizon": 0.2},
    "reviewer":   {"reasoning": 0.6, "coding": 0.4},
    "qa":         {"reasoning": 0.6, "coding": 0.4},
    "debugger":   {"reasoning": 0.6, "coding": 0.4},
    "planner":    {"research": 0.5, "reasoning": 0.5},
    "architect":  {"research": 0.4, "reasoning": 0.4, "long_horizon": 0.2},
    "designer":   {"design_layout": 1.0},
    "ui":         {"design_layout": 1.0},
    "researcher": {"research": 0.7, "data_analysis": 0.3},
    "analyst":    {"data_analysis": 0.7, "reasoning": 0.3},
    "browser":    {"browser": 0.7, "long_horizon": 0.3},
}

# ── 별칭 테이블 (자동 매칭 실패 케이스 수동 보정) ──────────────────────
# LiveBench 게시명이 우리 등록명과 접미사 규칙으로 안 맞는 것만 넣는다.
MODEL_ALIASES = {
    "muse-spark-1.3-contributor": "muse-spark-1.3",
    "muse-spark-1.3-contributor-free": "muse-spark-1.3",
    "deepseek-v4.1-flash": "deepseek-v4.1-flash",
}

# ── 이름 정규화 ────────────────────────────────────────────────────────
_SUFFIX_TOKENS = {
    "max", "min", "high", "xhigh", "medium", "low", "auto", "thinking",
    "effort", "preview", "exp", "instruct", "chat", "contributor", "free",
    "latest", "highspeed", "vision", "contributor", "pro",
}
_DATEISH = re.compile(r'^\d{4}$|^\d{2,4}k$|^v\d+$|^\d+\.\d+$')


def normalize(model_id: str) -> str:
    """케이스/구두점 무시 정규화 — 'Qwen3.8-Max' → 'qwen38max'."""
    return re.sub(r'[-_. ]+', '', (model_id or "").lower())


def _variant_keys(name: str) -> set:
    """이름의 정규화 변형 집합: 원형 + 접미사 제거형.

    'deepseek-v4.1-flash-max' → {deepseekv41flashmax, deepseekv41flash}
    'glm-5.3'                 → {glm53}
    """
    out = {normalize(name)}
    parts = [p for p in re.split(r'[-_. ]+', (name or "").lower()) if p]
    while parts and (parts[-1] in _SUFFIX_TOKENS or _DATEISH.match(parts[-1])):
        parts.pop()
        if parts:
            out.add(normalize("".join(parts)))
    return {x for x in out if len(x) >= 5}


class LiveBenchSource:
    """LiveBench 리더보드 기반 실측 점수 소스.

    사용:
        src = get_livebench_source()
        score = src.score("deepseek-v4.1-flash", "coding")   # 0..1 or None
        axes  = src.role_axes("developer")                    # {'coding':0.6,...}
    """

    def __init__(self, cache_path: Path = None, ttl: float = None):
        self._cache_path = cache_path or _CACHE_PATH
        self._ttl = _CACHE_TTL_S if ttl is None else ttl
        self._v1: dict = {}          # lb 모델명 → {task: score}
        self._v2: dict = {}
        self._v1_cat: dict = {}
        self._index: dict = {}       # 정규화키 → (lb명, 'v1'|'v2')
        self._v2_rel = _V2_REL
        self._loaded_at = 0.0
        self._ok = False
        self.load()

    # ── 저장/로드 ──

    def _read_cache(self) -> Optional[dict]:
        try:
            if self._cache_path.exists():
                return json.loads(self._cache_path.read_text(encoding="utf-8"))
        except Exception as e:
            _logger.warning("livebench: cache read failed: %s", e)
        return None

    def _write_cache(self):
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps({
                "ts": time.time(),
                "v1_rel": _V1_REL, "v2_rel": _V2_REL,
                "v1": self._v1, "v2": self._v2, "v1_cat": self._v1_cat,
            }, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            _logger.warning("livebench: cache write failed: %s", e)

    # ── 네트워크 ──

    @staticmethod
    def _get(url: str) -> str:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")

    @staticmethod
    def _parse_table(text: str) -> dict:
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            return {}
        cols = [c.strip() for c in rows[0][1:]]
        out = {}
        for r in rows[1:]:
            if not r or not r[0].strip():
                continue
            vals = {}
            for i, k in enumerate(cols, 1):
                if i < len(r) and r[i] not in ("", "None", "nan"):
                    try:
                        vals[k] = float(r[i])
                    except ValueError:
                        pass
            if vals:
                out[r[0].strip()] = vals
        return out

    def _fetch_all(self) -> bool:
        """v1 + v2 테이블을 내려받는다. 하나라도 실패하면 부분 사용."""
        got = False
        try:
            self._v1 = self._parse_table(self._get(f"{_V1_BASE}/table_{_V1_REL}.csv"))
            try:
                self._v1_cat = json.loads(self._get(f"{_V1_BASE}/categories_{_V1_REL}.json"))
            except Exception:
                self._v1_cat = {}
            got = got or bool(self._v1)
        except Exception as e:
            _logger.info("livebench: v1 fetch failed: %s", e)

        for rel in _V2_REL_CANDIDATES:
            try:
                v2 = self._parse_table(self._get(f"{_V2_BASE}/table_{rel}.csv"))
                if v2:
                    self._v2 = v2
                    self._v2_rel = rel
                    got = True
                    break
            except Exception as e:
                _logger.info("livebench: v2 fetch failed (%s): %s", rel, e)
        return got

    def _build_index(self):
        idx = {}
        for src, data in (("v2", self._v2), ("v1", self._v1)):   # v2 우선
            for name in data:
                for k in _variant_keys(name):
                    if k not in idx:
                        idx[k] = (name, src)
        self._index = idx

    def load(self, force: bool = False):
        """캐시 → 네트워크 순으로 데이터를 준비한다."""
        cache = self._read_cache()
        if cache and not force and (time.time() - float(cache.get("ts", 0) or 0)) < self._ttl:
            self._v1 = cache.get("v1") or {}
            self._v2 = cache.get("v2") or {}
            self._v1_cat = cache.get("v1_cat") or {}
            self._v2_rel = cache.get("v2_rel") or _V2_REL
            self._loaded_at = cache.get("ts", 0.0)
        else:
            if self._fetch_all():
                self._write_cache()
                self._loaded_at = time.time()
            elif cache:                      # 네트워크 실패 → 묵은 캐시라도 사용
                self._v1 = cache.get("v1") or {}
                self._v2 = cache.get("v2") or {}
                self._v1_cat = cache.get("v1_cat") or {}
                self._v2_rel = cache.get("v2_rel") or _V2_REL
                self._loaded_at = cache.get("ts", 0.0)
        self._build_index()
        self._ok = bool(self._v1 or self._v2)
        if self._ok:
            _logger.info(
                "livebench: loaded v1=%d models, v2=%d models (rel v1=%s, v2=%s)",
                len(self._v1), len(self._v2), _V1_REL, getattr(self, "_v2_rel", _V2_REL),
            )
        return self._ok

    # ── 조회 ──

    @property
    def available(self) -> bool:
        return self._ok

    def resolve(self, our_model: str) -> Optional[tuple]:
        """우리 등록 모델명 → (LiveBench명, 'v1'|'v2') 또는 None."""
        if not our_model:
            return None
        alias = MODEL_ALIASES.get(our_model)
        for cand in ([alias] if alias else []) + [our_model]:
            for k in _variant_keys(cand):
                if k in self._index:
                    return self._index[k]
        return None

    def raw_field_scores(self, our_model: str) -> dict:
        """LiveBench 원점수(0~100) dict. v2 우선, 없으면 v1 세부태스크."""
        hit = self.resolve(our_model)
        if not hit:
            return {}
        name, src = hit
        return dict(self._v2.get(name) if src == "v2" else self._v1.get(name) or {})

    def capability_scores(self, our_model: str) -> dict:
        """우리 capability 축별로 정규화한 점수(0~1)."""
        hit = self.resolve(our_model)
        if not hit:
            return {}
        name, src = hit
        acc = {}
        if src == "v2":
            v = self._v2.get(name) or {}
            for field, score in v.items():
                caps = V2_FIELD_TO_CAPABILITY.get(field)
                if not caps:
                    continue
                for cap in caps:
                    acc.setdefault(cap, []).append(score)
        else:
            v = self._v1.get(name) or {}
            for cat, tasks in (self._v1_cat or {}).items():
                caps = V1_CATEGORY_TO_CAPABILITY.get(cat)
                if not caps:
                    continue
                xs = [v[t] for t in tasks if t in v]
                if xs:
                    avg = sum(xs) / len(xs)
                    for cap in caps:
                        acc.setdefault(cap, []).append(avg)
        return {cap: max(0.0, min(1.0, sum(xs) / len(xs) / 100.0))
                for cap, xs in acc.items() if xs}

    def score(self, our_model: str, capability: str) -> Optional[float]:
        """특정 capability 축의 LiveBench 점수(0~1). 없으면 None."""
        return self.capability_scores(our_model).get(capability)

    @staticmethod
    def role_axes(role: str) -> dict:
        """노드 역할 → LiveBench 분야 가중치. 미지정 역할은 coding 기본."""
        r = (role or "").lower()
        if r in ROLE_LIVEBENCH_AXES:
            return ROLE_LIVEBENCH_AXES[r]
        for key, axes in ROLE_LIVEBENCH_AXES.items():
            if key in r:
                return axes
        if any(k in r for k in ("design", "ui", "ux", "front")):
            return ROLE_LIVEBENCH_AXES["designer"]
        if any(k in r for k in ("review", "qa", "audit", "test")):
            return ROLE_LIVEBENCH_AXES["reviewer"]
        if any(k in r for k in ("plan", "architect")):
            return ROLE_LIVEBENCH_AXES["planner"]
        return ROLE_LIVEBENCH_AXES["developer"]

    def role_score(self, our_model: str, role: str) -> Optional[float]:
        """역할 가중 평균 LiveBench 점수(0~1). 데이터 없으면 None.

        역할별로 다른 분야를 보므로, 같은 모델이라도 역할이 다르면
        점수가 달라진다 — 이것이 '노드에 맞는 모델 선별'의 핵심이다.
        """
        hit = self.resolve(our_model)
        if not hit:
            return None
        name, src = hit
        axes = self.role_axes(role)
        tot_w, acc = 0.0, 0.0
        if src == "v2":
            v = self._v2.get(name) or {}
            for field, w in axes.items():
                x = v.get(field)
                if x is not None:
                    acc += x * w
                    tot_w += w
        else:
            caps = self.capability_scores(our_model)
            # v1 은 capability 로 접은 뒤 축 이름을 근사 대응
            cap_of = {"coding": "coding", "reasoning": "reasoning",
                      "long_horizon": "agentic", "research": "context",
                      "data_analysis": "coding", "design_layout": "coding",
                      "browser": "tool_use"}
            for field, w in axes.items():
                x = caps.get(cap_of.get(field, field))
                if x is not None:
                    acc += x * 100.0 * w
                    tot_w += w
        if tot_w <= 0:
            return None
        return max(0.0, min(1.0, (acc / tot_w) / 100.0))

    def stats(self) -> dict:
        return {
            "available": self._ok,
            "v1_models": len(self._v1),
            "v2_models": len(self._v2),
            "v1_rel": _V1_REL,
            "v2_rel": self._v2_rel,
            "cache": str(self._cache_path),
            "loaded_at": self._loaded_at,
            "age_s": round(time.time() - self._loaded_at, 1) if self._loaded_at else None,
        }


# ── 싱글턴 ──
_singleton: Optional[LiveBenchSource] = None
_singleton_lock = None


def get_livebench_source() -> LiveBenchSource:
    """프로세스 전역 싱글턴 (파일 1회 로드, 이후 순수 메모리 조회)."""
    global _singleton, _singleton_lock
    if _singleton_lock is None:
        import threading
        _singleton_lock = threading.Lock()
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = LiveBenchSource()
    return _singleton
