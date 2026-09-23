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
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

_logger = logging.getLogger("api.laya_client")

DEFAULT_LAYA_URL = "http://127.0.0.1:8765"
DEFAULT_TIMEOUT = 3.0  # 3.0s to allow CPU inference as well as GPU


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
                    self._is_alive = (data.get("status") == "ok")
                    return self._is_alive
        except Exception:
            pass

        self._is_alive = False
        return False

    def prune_mcp(self, prompt: str, available_mcps: List[str]) -> List[str]:
        """Ask Laya which MCP servers are genuinely needed for the given prompt.
        
        Returns a list of needed MCP server names.
        If Laya is offline, falls back to safe keyword heuristics.
        """
        if not prompt or not available_mcps:
            return []

        # 1. Try Laya service
        if self.is_healthy():
            try:
                payload = json.dumps({
                    "prompt": prompt,
                    "available_mcps": available_mcps
                }).encode("utf-8")
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
        prompt_lower = prompt.lower()
        needed = []

        # Figma
        if "figma" in available_mcps:
            if any(k in prompt_lower for k in ["figma", "피그마", "figma_token", "디자인 파일"]):
                needed.append("figma")

        # Daon Design
        if "daon-design" in available_mcps:
            if any(k in prompt_lower for k in ["daon-design", "디자인 토큰", "style card", "css 스타일", "스타일가이드"]):
                needed.append("daon-design")

        # Serena (AST exploration)
        if "serena" in available_mcps:
            if any(k in prompt_lower for k in ["serena", "세레나", "심층 분석", "ast 탐색", "코드베이스 전체 분석"]):
                needed.append("serena")

        # Context7 (Documentation)
        if "context7" in available_mcps:
            if any(k in prompt_lower for k in ["context7", "라이브러리 문서", "api 문서 검색"]):
                needed.append("context7")

        # Stitch
        if "stitch" in available_mcps:
            if any(k in prompt_lower for k in ["stitch", "스티치", "워크플로우 합성"]):
                needed.append("stitch")

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

    def batch_classify(self, items: List[str], categories: Dict[str, str], instruction: str = "Classify this item") -> List[str]:
        """Classify a batch of text items into one of the categories."""
        if not self.is_healthy():
            return []

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
                    return data.get("results", [])
        except Exception as e:
            _logger.warning("Laya batch_classify failed: %s", e)
        return []


# Global singleton instance
laya_client = LayaClient()
