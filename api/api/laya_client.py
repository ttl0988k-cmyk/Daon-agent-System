"""
api.laya_client - Client library for Laya System 1 Decision Engine.

Provides ultra-fast (~30ms) typed decisions, MCP pruning, and model routing.
Designed with strict Graceful Fallback:
- Sub-500ms timeout
- Automatic health check caching
- If Laya service is not running, falls back safely to heuristic rules without blocking.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Union

_logger = logging.getLogger("api.laya_client")

DEFAULT_LAYA_URL = "http://127.0.0.1:8765"
DEFAULT_TIMEOUT = 3.0  # 3.0s to allow CPU inference as well as GPU


# [FIX 2026-10-13] MCP 카탈로그 — 신규 MCP는 JSON 1줄로 온보딩, Laya 코드 수정 불필요.
# data/mcp_catalog.json을 프로세스당 1회 로드해 캐시한다. 파일이 없거나 깨져도 fail-open({}).
_MCP_CATALOG_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def _load_mcp_catalog() -> Dict[str, Dict[str, Any]]:
    """Load data/mcp_catalog.json once per process (module cache). Fail-open -> {}."""
    from pathlib import Path
    import os

    global _MCP_CATALOG_CACHE
    if _MCP_CATALOG_CACHE is not None:
        return _MCP_CATALOG_CACHE

    catalog: Dict[str, Dict[str, Any]] = {}
    try:
        cand_paths = [
            Path("data/mcp_catalog.json"),
            Path(__file__).resolve().parent.parent.parent / "data" / "mcp_catalog.json",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "daon-agent-system" / "resources" / "data" / "mcp_catalog.json",
            Path(os.environ.get("LOCALAPPDATA", "")) / "DAON Agent System" / "data" / "mcp_catalog.json",
        ]
        for cp in cand_paths:
            if cp.exists():
                with open(cp, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for sid, meta in raw.items():
                    if sid.startswith("_"):
                        continue
                    if isinstance(meta, dict):
                        catalog[sid] = meta
                break
    except Exception as e:
        _logger.warning("mcp_catalog.json load skipped (fail-open): %s", e)

    _MCP_CATALOG_CACHE = catalog
    return catalog


def get_mcp_desc_map(sids: List[str]) -> Dict[str, str]:
    """[FIX 2026-10-13] Build {server_id: criteria_text} from the MCP catalog.

    streaming_tools의 Laya-MCP-Preselect가 prune_mcp에 넘길 설명 dict를 만든다.
    카탈로그에 없는 서버는 건너뛴다(기존 label/KNOWN 경로가 서버측에서 처리). Fail-open -> {}.
    """
    catalog = _load_mcp_catalog()
    desc_map: Dict[str, str] = {}
    for sid in sids:
        meta = catalog.get(sid)
        if not isinstance(meta, dict):
            continue
        desc_en = str(meta.get("desc_en") or "").strip()
        desc_ko = str(meta.get("desc_ko") or "").strip()
        text = f"{desc_en} ({desc_ko})" if desc_en and desc_ko else (desc_en or desc_ko)
        if text:
            desc_map[sid] = text
    return desc_map


class LayaClient:
    """Thread-safe client for Laya decision daemon with caching and graceful fallback."""

    def __init__(self, base_url: str = DEFAULT_LAYA_URL, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._last_health_check = 0.0
        self._is_alive = False

    def is_healthy(self, max_cache_age: float = 3.0) -> bool:
        """Check if Laya service is alive (cached for max_cache_age seconds)."""
        now = time.time()
        if now - self._last_health_check < max_cache_age:
            return self._is_alive

        self._last_health_check = now
        try:
            req = urllib.request.Request(f"{self.base_url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=0.15) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    # [FIX 2026-10-01] Treat "booting" as alive too. While the
                    # Laya model loads (30~60s) the daemon already owns port
                    # 8765, so it IS running -- respawning here used to create
                    # a duplicate daemon (~1.85GB VRAM wasted).
                    self._is_alive = data.get("status") in ("ok", "booting")
                    return self._is_alive
        except Exception:
            pass

        self._is_alive = False
        return False

    # Dynamic fallback keywords for offline heuristic pruning
    FALLBACK_KEYWORDS: Dict[str, List[str]] = {
        "figma": ["figma", "피그마", "figma_token", "디자인 파일", "ui 레이아웃"],
        "daon-design": ["daon-design", "디자인 토큰", "style card", "css 스타일", "스타일가이드", "디자인 컴포넌트"],
        "serena": ["serena", "세레나", "심층 분석", "ast 탐색", "코드베이스 전체 분석", "심볼 탐색"],
        "context7": ["context7", "라이브러리 문서", "api 문서 검색", "3rd-party doc"],
        "stitch": ["stitch", "스티치", "워크플로우 합성", "다단계 파이프라인"],
        "rea": ["rea", "리버스 엔지니어링", "react 분석", "컴포넌트 디컴파일", "역공학"],
        "reticle": ["reticle", "레티클", "좌표 타겟팅", "크로스헤어", "화면 타겟", "정밀 조준"],
        "playmcp-gateway": ["playmcp", "카카오톡", "네이버 검색", "메시지 전송", "외부 포털"],
        "filesystem": ["filesystem", "파일 탐색", "디렉토리 목록", "로컬 파일"],
        "playwright": ["playwright", "브라우저 자동화", "웹 스크래핑", "스크린샷 촬영"],
        "memory": ["memory", "장기 기억", "기억해줘", "지식 그래프", "사용자 선호"],
        "github": ["github", "깃허브", "pull request", "이슈 생성", "pr 검토"],
        "slack": ["slack", "슬랙", "채널 알림", "슬랙 메시지"],
        "postgres": ["postgres", "postgresql", "sql 쿼리", "db 테이블"],
        "sqlite": ["sqlite", "sqlite3", "로컬 db"],
    }

    def prune_mcp(
        self,
        prompt: str,
        available_mcps: List[str],
        mcp_descriptions: Optional[Dict[str, str]] = None
    ) -> List[str]:
        """Ask Laya which MCP servers are genuinely needed for the given prompt.
        
        Returns a list of needed MCP server names.
        If Laya is offline, falls back to dynamic keyword heuristics.
        """
        if not prompt or not available_mcps:
            return []

        # 1. Try Laya service
        if self.is_healthy():
            try:
                body_dict: Dict[str, Any] = {
                    "prompt": prompt,
                    "available_mcps": available_mcps,
                }
                if mcp_descriptions:
                    body_dict["mcp_descriptions"] = mcp_descriptions

                payload = json.dumps(body_dict).encode("utf-8")
                req = urllib.request.Request(
                    f"{self.base_url}/prune_mcp",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if resp.status == 200:
                        result = json.loads(resp.read().decode("utf-8"))
                        needed = result.get("needed_mcps", [])
                        _logger.info("Laya pruned MCPs: %s -> %s", available_mcps, needed)
                        return [m for m in needed if m in available_mcps]
            except Exception as e:
                _logger.warning("Laya prune_mcp request failed, falling back to heuristics: %s", e)

        # 2. Graceful Fallback Heuristic
        return self._fallback_prune_mcp(prompt, available_mcps)

    def _fallback_prune_mcp(self, prompt: str, available_mcps: List[str]) -> List[str]:
        """Fallback heuristics if Laya daemon is not running."""
        # [FIX 2026-10-13] 라벨/카탈로그 경로 탐색용 import — 기존엔 이 함수 안에서 Path/os가
        # 미import라 NameError가 except로 삼켜져 라벨 매칭이 조용히 죽어 있었다.
        from pathlib import Path
        import os

        prompt_lower = prompt.lower()
        needed = []

        # Try reading data/mcp_servers.json labels if available for dynamic keyword matching
        mcp_labels: Dict[str, str] = {}
        try:
            cand_paths = [
                Path("data/mcp_servers.json"),
                Path(__file__).resolve().parent.parent.parent / "data" / "mcp_servers.json",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "daon-agent-system" / "resources" / "data" / "mcp_servers.json",
            ]
            for cp in cand_paths:
                if cp.exists():
                    with open(cp, "r", encoding="utf-8") as f:
                        for srv in json.load(f):
                            sid = srv.get("server_id")
                            lbl = srv.get("label")
                            if sid and lbl:
                                mcp_labels[sid] = lbl
                    break
        except Exception:
            pass

        # [FIX 2026-10-13] MCP 카탈로그 keywords — 신규 MCP는 JSON 1줄로 온보딩, Laya 코드 수정 불필요
        catalog = _load_mcp_catalog()

        for mcp in available_mcps:
            mcp_id_lower = mcp.lower()
            # 1. Check known keywords
            keywords = self.FALLBACK_KEYWORDS.get(mcp_id_lower, [])
            if any(k in prompt_lower for k in keywords):
                needed.append(mcp)
                continue

            # 2. Check direct MCP name appearance
            clean_name = mcp_id_lower.replace("-", " ").replace("_", " ")
            if mcp_id_lower in prompt_lower or clean_name in prompt_lower:
                needed.append(mcp)
                continue

            # 3. Check MCP catalog keywords (신규 MCP 온보딩용) [FIX 2026-10-13]
            cat_meta = catalog.get(mcp)
            if isinstance(cat_meta, dict):
                cat_kws = cat_meta.get("keywords") or []
                if any(str(k).lower() in prompt_lower for k in cat_kws if k):
                    needed.append(mcp)
                    continue

            # 4. Check mcp_servers.json label keywords (if label tokens appear in prompt)
            lbl = mcp_labels.get(mcp, "")
            if lbl:
                tokens = [t.lower() for t in lbl.split() if len(t) >= 2]
                if any(t in prompt_lower for t in tokens):
                    needed.append(mcp)
                    continue

        return needed

    def pre_route_user_prompt(self, prompt: str) -> Dict[str, Any]:
        """System 1 inline pre-routing for Raon (streaming agent).
        
        Evaluates prompt intent in ~30ms to decide:
        - intent: 'conversation' | 'coding' | 'worker_task' | 'design_ui'
        - confidence: float
        - latency_ms: float
        """
        if not prompt:
            return {"intent": "conversation", "confidence": 1.0, "latency_ms": 0.0}

        if self.is_healthy():
            try:
                payload = json.dumps({"prompt": prompt}).encode("utf-8")
                req = urllib.request.Request(
                    f"{self.base_url}/pre_route",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                _logger.warning("Laya pre_route request failed, using fast fallback: %s", e)

        # Fallback rule-based heuristic
        p_lower = prompt.lower()
        if any(w in p_lower for w in ["워커", "하네스", "worker", "harness", "배경 작업", "자율 작업"]):
            return {"intent": "worker_task", "confidence": 0.8, "fallback": True}
        if any(w in p_lower for w in ["디자인", "css", "스타일", "figma", "피그마", "color", "layout"]):
            return {"intent": "design_ui", "confidence": 0.8, "fallback": True}
        if any(w in p_lower for w in ["코드", "수정", "버그", "작성", "파일", "스크립트", "git", "빌드", "테스트", "def ", "class "]):
            return {"intent": "coding", "confidence": 0.8, "fallback": True}
        return {"intent": "conversation", "confidence": 0.7, "fallback": True}

    @staticmethod
    def _normalize_decide_payload(raw_state: Any, raw_questions: Any) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Normalize freeform state & questions into Laya predict schema."""
        # 1. Normalize state: ensure dict with 'text' or 'prompt'
        norm_state: Dict[str, Any] = {}
        if isinstance(raw_state, str):
            norm_state = {"text": raw_state}
        elif isinstance(raw_state, dict):
            norm_state = dict(raw_state)
            if not any(k in norm_state for k in ["text", "prompt"]):
                norm_state["text"] = " ".join(f"{k}: {v}" for k, v in norm_state.items()) or "None"
        else:
            norm_state = {"text": str(raw_state)}

        # 2. Normalize questions: ensure required Laya schema (instructions, type, criteria)
        norm_questions: Dict[str, Any] = {}
        if isinstance(raw_questions, dict):
            for q_key, q_def in raw_questions.items():
                if isinstance(q_def, dict):
                    qd = dict(q_def)
                    if not qd.get("instructions"):
                        qd["instructions"] = f"Determine the answer for {q_key}"
                    if not qd.get("type"):
                        qd["type"] = "choice"
                    if qd.get("type") == "choice":
                        if "criteria" not in qd or not qd["criteria"]:
                            qd["criteria"] = {"yes": "Yes", "no": "No"}
                        elif isinstance(qd["criteria"], (list, tuple)):
                            qd["criteria"] = {str(c): str(c) for c in qd["criteria"]}
                    norm_questions[q_key] = qd
                elif isinstance(q_def, (list, tuple)):
                    norm_questions[q_key] = {
                        "type": "choice",
                        "instructions": f"Select the best option for {q_key}",
                        "criteria": {str(opt): str(opt) for opt in q_def}
                    }
                elif isinstance(q_def, str):
                    if "/" in q_def:
                        opts = [o.strip() for o in q_def.split("/") if o.strip()]
                        norm_questions[q_key] = {
                            "type": "choice",
                            "instructions": f"Determine {q_key}",
                            "criteria": {opt: opt for opt in opts}
                        }
                    else:
                        norm_questions[q_key] = {
                            "type": "choice",
                            "instructions": q_def,
                            "criteria": {"yes": "Yes", "no": "No"}
                        }
        return norm_state, norm_questions

    def decide(self, state: Dict[str, Any], questions: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Perform general typed decision with Laya.
        
        Automatically normalizes freeform state and questions to match Laya predict specification.
        """
        if not self.is_healthy():
            return None

        try:
            norm_state, norm_questions = self._normalize_decide_payload(state, questions)
            payload = json.dumps({"state": norm_state, "questions": norm_questions}).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/decide",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            _logger.warning("Laya decide request failed: %s", e)
        return None

    def batch_classify(
        self,
        items: List[str],
        categories: Dict[str, str],
        instruction: str = "Classify this item",
        return_details: bool = False
    ) -> Union[List[str], Dict[str, Any]]:
        """Classify a batch of text items into one of the categories.
        
        Args:
            items: List of text strings to classify.
            categories: Dict mapping category ID -> description.
            instruction: Classification prompt/instruction.
            return_details: If True, returns dict with results and probabilities.
        """
        if not self.is_healthy():
            return {"results": [], "probabilities": []} if return_details else []

        try:
            payload = json.dumps({
                "items": items,
                "categories": categories,
                "instruction": instruction
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/batch_classify",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=max(self.timeout, len(items) * 2.5)) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if return_details:
                        return data
                    return data.get("results", [])
        except Exception as e:
            _logger.warning("Laya batch_classify failed: %s", e)
        return {"results": [], "probabilities": []} if return_details else []

    _skills_meta_cache: Optional[List[Dict[str, Any]]] = None
    _skills_cache_time: float = 0.0
    SKILLS_CACHE_TTL: float = 30.0  # 30초마다 skills/ 디렉터리 자동 재스캔 (새 스킬 자동 편입)

    def invalidate_skills_cache(self) -> None:
        """Explicitly invalidate skills cache (e.g. after self-evolution)."""
        self._skills_meta_cache = None
        self._skills_cache_time = 0.0

    def _get_skills_meta(self) -> List[Dict[str, Any]]:
        """Load and cache available skills metadata from profile and bundle skills/ directories with auto-refresh."""
        now = time.time()
        if self._skills_meta_cache is not None and (now - self._skills_cache_time) < self.SKILLS_CACHE_TTL:
            return self._skills_meta_cache

        skills_list = []
        try:
            from pathlib import Path
            import os

            # 1. Candidate skill directories (User profile skills first, then local repo and bundles)
            skills_dirs = [
                Path.home() / ".hermes" / "profiles" / "raon" / "skills",
                Path.home() / ".hermes" / "skills",
                Path(os.environ.get("LOCALAPPDATA", "")) / "DAON Agent System" / "skills",
                Path(r"C:\daon\Daon agent System\skills"),
                Path.cwd() / "skills",
                Path(__file__).resolve().parent.parent.parent / "skills",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "daon-agent-system" / "resources" / "skills",
            ]

            # Try loading snapshot metadata for faster and richer descriptions if available
            snap_meta = {}
            try:
                snap_path = Path.home() / ".hermes" / "profiles" / "raon" / ".skills_prompt_snapshot.json"
                if snap_path.exists():
                    import json
                    with open(snap_path, "r", encoding="utf-8") as sf:
                        sdata = json.load(sf)
                        for sk_entry in sdata.get("skills", []):
                            fname = sk_entry.get("frontmatter_name") or sk_entry.get("skill_name")
                            if fname:
                                snap_meta[fname] = sk_entry
            except Exception:
                pass

            seen_names = set()

            for sdir in skills_dirs:
                if not sdir.is_dir():
                    continue
                try:
                    for skill_md in sdir.rglob("SKILL.md"):
                        folder = skill_md.parent
                        name = folder.name
                        if name in seen_names:
                            continue

                        # Resolve category: relative path or parent folder
                        try:
                            rel = skill_md.relative_to(sdir)
                            cat = rel.parts[0] if len(rel.parts) > 2 else (folder.parent.name if folder.parent != sdir else "General")
                        except Exception:
                            cat = "General"

                        desc = ""
                        triggers = []

                        # Check snapshot metadata first
                        if name in snap_meta:
                            desc = snap_meta[name].get("description", "")
                            cat = snap_meta[name].get("category", cat)

                        # Check skill.yaml
                        yaml_path = folder / "skill.yaml"
                        if yaml_path.exists():
                            try:
                                import yaml
                                with open(yaml_path, "r", encoding="utf-8") as yf:
                                    ydata = yaml.safe_load(yf) or {}
                                    desc = desc or ydata.get("description") or ydata.get("when_to_use") or ""
                                    triggers = ydata.get("trigger") or []
                            except Exception:
                                pass

                        # Fallback parsing frontmatter or first non-empty lines
                        if not desc:
                            try:
                                content = skill_md.read_text(encoding="utf-8", errors="replace")
                                if content.startswith("---"):
                                    parts = content.split("---", 2)
                                    if len(parts) >= 3:
                                        import yaml
                                        fm = yaml.safe_load(parts[1]) or {}
                                        desc = fm.get("description") or fm.get("when_to_use") or ""
                                        if fm.get("name"):
                                            name = fm.get("name")
                                if not desc:
                                    lines = [l.strip("# -*").strip() for l in content.splitlines() if l.strip() and not l.startswith("---")]
                                    desc = lines[1] if len(lines) > 1 else (lines[0] if lines else name)
                            except Exception:
                                desc = name

                        seen_names.add(name)
                        skills_list.append({
                            "name": name,
                            "category": cat,
                            "description": desc[:150],
                            "triggers": [t.lower() for t in triggers if isinstance(t, str)],
                            "path": skill_md,
                        })
                except Exception as _dir_err:
                    _logger.debug("Error scanning skills dir %s: %s", sdir, _dir_err)

        except Exception as e:
            _logger.warning("Failed to load skills meta for Laya: %s", e)

        _logger.info("Laya loaded %d total skills across profile and bundle directories", len(skills_list))
        self._skills_meta_cache = skills_list
        self._skills_cache_time = now
        return skills_list

    GENERIC_STOPWORDS = {
        "다온", "daon", "라온", "raon", "스킬", "skill", "skills",
        "가이드", "설명", "도움", "도우미", "사용법", "튜토리얼",
        "guide", "tutorial", "help", "info", "정보"
    }

    TERM_SYNONYMS = {
        "하네스": "harness", "카탈로그": "catalog", "애플": "apple", "메모": "notes",
        "브라우저": "browser", "테스트": "test", "검증": "verify", "배포": "deploy",
        "디자인": "design", "이미지": "image", "데이터": "data", "보안": "security",
        "깃": "git", "도커": "docker", "모바일": "mobile", "메모리": "memory",
    }

    def match_skill(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Ultra-fast skill pre-fetch using Ranker-first & Token-indexed ensemble (~30-50ms)."""
        if not prompt or len(prompt.strip()) < 2:
            return None

        t0 = time.time()
        skills = self._get_skills_meta()
        if not skills:
            return None

        p_lower = prompt.lower()
        skills_by_name = {s["name"]: s for s in skills}

        # 1. 0ms Fast Token & Synonym Extraction (pure dictionary & set lookups, no 400-regex loop)
        raw_tokens = re.findall(r'[a-zA-Z0-9가-힣_\-]+', p_lower)
        token_set = set(raw_tokens)

        # 1-A. Exact Standalone Name Match (0ms dict lookup)
        # Matches when the user typed the exact skill name as a discrete word (e.g. "taste 스킬 가이드")
        for tok in raw_tokens:
            tok_clean = tok.strip("-_")
            if tok_clean in skills_by_name and tok_clean not in self.GENERIC_STOPWORDS:
                sobj = skills_by_name[tok_clean]
                snippet = self._read_skill_snippet(sobj["path"])
                return {
                    "skill": tok_clean,
                    "category": sobj["category"],
                    "snippet": snippet,
                    "source": "exact_name",
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                }

        # 1-B. Full Synonym Name Match (0ms set operations) - e.g. "애플 메모" -> "apple-notes"
        expanded_tokens = set(token_set)
        for t in token_set:
            if t in self.TERM_SYNONYMS:
                expanded_tokens.add(self.TERM_SYNONYMS[t])
        for s in skills:
            s_tokens = set(s["name"].lower().replace("-", " ").replace("_", " ").split())
            if len(s_tokens) >= 2 and s_tokens.issubset(expanded_tokens):
                snippet = self._read_skill_snippet(s["path"])
                return {
                    "skill": s["name"],
                    "category": s["category"],
                    "snippet": snippet,
                    "source": "synonym_name",
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                }

        # 2. First-Class 8766 Semantic Ranker Query (~40-70ms)
        ranker_res = []
        margin = 0.0
        try:
            import urllib.request as _ur
            payload = json.dumps({"prompt": prompt[:300], "top_n": 8, "min_margin": 0.0}).encode("utf-8")
            req = _ur.Request("http://127.0.0.1:8766/rank_skills", data=payload, headers={"Content-Type": "application/json"})
            with _ur.urlopen(req, timeout=0.18) as r:
                rdata = json.loads(r.read().decode("utf-8"))
                ranker_res = rdata.get("skills", [])
                margin = float(rdata.get("margin", 0.0))
        except Exception:
            pass

        t1 = ranker_res[0] if ranker_res else None
        t2 = ranker_res[1] if len(ranker_res) > 1 else None
        t1_score = float(t1["score"]) if t1 else 0.0
        t2_score = float(t2["score"]) if t2 else 0.0
        diff = round(t1_score - t2_score, 4)

        # 2-A. Ranker High-Margin Fast-Path (Laya & Deep Scan Bypass)
        if t1:
            if (margin >= 0.048 and t1_score >= 0.84) or (diff >= 0.025 and t1_score >= 0.83) or (margin >= 0.038 and t1_score >= 0.87):
                sobj = skills_by_name.get(t1["name"])
                if sobj:
                    snippet = self._read_skill_snippet(sobj["path"])
                    return {
                        "skill": t1["name"],
                        "category": sobj["category"],
                        "snippet": snippet,
                        "source": "ranker_high_margin",
                        "margin": margin,
                        "diff": diff,
                        "score": t1_score,
                        "latency_ms": round((time.time() - t0) * 1000, 1),
                    }

        # 2-B. Concordance Fast-Path: ranker top1 shares meaningful non-stopword tokens with prompt
        if t1 and t1_score >= 0.82:
            t1_words = set(re.findall(r'[a-zA-Z0-9가-힣]+', t1["name"].lower()))
            overlap = (t1_words & expanded_tokens) - self.GENERIC_STOPWORDS
            if overlap:
                sobj = skills_by_name.get(t1["name"])
                if sobj:
                    snippet = self._read_skill_snippet(sobj["path"])
                    return {
                        "skill": t1["name"],
                        "category": sobj["category"],
                        "snippet": snippet,
                        "source": "concordance_fast_path",
                        "ranker_score": t1_score,
                        "latency_ms": round((time.time() - t0) * 1000, 1),
                    }

        # 2-C. Unrelated Query Fast-Drop (무관 질의 조기 탈락)
        if (margin < 0.035) and (diff < 0.020) and (t1_score < 0.85):
            return None

        # 3. Non-Generic Triggers on Candidate Subset Only (no 400-item scan)
        for cand in ranker_res[:5]:
            sobj = skills_by_name.get(cand["name"])
            if sobj and sobj["triggers"]:
                for tr in sobj["triggers"]:
                    tr_l = tr.lower()
                    if tr_l not in self.GENERIC_STOPWORDS and tr_l in p_lower:
                        snippet = self._read_skill_snippet(sobj["path"])
                        return {
                            "skill": sobj["name"],
                            "category": sobj["category"],
                            "snippet": snippet,
                            "source": "trigger_keyword",
                            "latency_ms": round((time.time() - t0) * 1000, 1),
                        }

        # 4. Fallback to Laya Semantic Classification (only for borderline ambiguous cases)
        cand_objs = [skills_by_name[r["name"]] for r in ranker_res[:6] if r["name"] in skills_by_name]
        if self.is_healthy() and cand_objs:
            try:
                cand_descs = {s["name"]: f"{s['name'].replace('-', ' ')} — {s['description'][:80]}" for s in cand_objs}
                cand_descs["none"] = "일반 대화 및 관련 스킬 없음 (None of the above)"
                res = self.batch_classify(
                    [prompt[:200]],
                    cand_descs,
                    instruction="사용자의 작업에 가장 부합하는 전용 스킬:"
                )
                if res and isinstance(res, list) and res[0] != "none":
                    matched_name = res[0]
                    matched_obj = skills_by_name.get(matched_name)
                    if matched_obj:
                        snippet = self._read_skill_snippet(matched_obj["path"])
                        return {
                            "skill": matched_name,
                            "category": matched_obj["category"],
                            "snippet": snippet,
                            "source": "laya_ensemble",
                            "latency_ms": round((time.time() - t0) * 1000, 1),
                        }
            except Exception as e:
                _logger.debug("Laya fallback batch_classify failed: %s", e)

        return None

    @staticmethod
    def _read_skill_snippet(path) -> str:
        """Extract a compact, actionable guideline snippet (~400 chars) from SKILL.md."""
        try:
            text = path.read_text(encoding="utf-8")
            lines = [l for l in text.splitlines() if not l.startswith("---") and l.strip()]
            snippet = "\n".join(lines[:15]).strip()
            return snippet[:500]
        except Exception:
            return ""


# Global singleton instance
laya_client = LayaClient()
