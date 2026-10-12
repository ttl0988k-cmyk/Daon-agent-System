"""
daon_runtime.laya_service - Standalone Ultra-fast Laya Decision Daemon for DAON.

Runs ModernBERT-large 421M non-autoregressive decision model on localhost:8765.
Provides:
- GET /health
- POST /prune_mcp
- POST /decide
- POST /batch_classify
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [LayaService] %(message)s"
)
_logger = logging.getLogger("laya_service")

PORT = 8765
MODEL_ID = "convaiinnovations/laya"

# Global model instance
_router = None
_device = "cpu"
_start_time = time.time()
_ready = False  # [FIX 2026-10-01] model-loaded flag; False means "booting"


def init_laya_model():
    """Initialize Laya model on GPU (if available) or CPU."""
    global _router, _device, _ready
    _logger.info("Initializing Laya Decision Model: %s ...", MODEL_ID)
    try:
        import torch
        if torch.cuda.is_available():
            _device = f"cuda ({torch.cuda.get_device_name(0)})"
            _logger.info("CUDA detected: %s", _device)
        else:
            _device = "cpu"
            _logger.info("No CUDA available, running on CPU")
    except Exception as e:
        _logger.warning("Torch check warning: %s", e)
        _device = "cpu"

    try:
        import os
        import glob
        import laya
        t0 = time.time()
        model_path = MODEL_ID
        cache_pattern = os.path.expanduser(r"~/.cache/huggingface/hub/models--convaiinnovations--laya/snapshots/*")
        snapshots = glob.glob(cache_pattern)
        if snapshots and os.path.isdir(snapshots[0]):
            model_path = snapshots[0]
            _logger.info("Using cached local model path: %s", model_path)
        _router = laya.load(model_path)
        _logger.info("Laya model loaded successfully in %.2f seconds on %s", time.time() - t0, _device)
    except Exception as e:
        _logger.error("Failed to load Laya model: %s", e, exc_info=True)
        _router = None
    finally:
        _ready = True  # [FIX 2026-10-01] booting -> ok/degraded


KNOWN_MCP_CRITERIA = {
    "figma": "Figma design frames, component styles, UI layouts, or UI design tokens (피그마 디자인 프레임·컴포넌트 스타일·UI 레이아웃).",
    "daon-design": "DAON UI design system style cards, color tokens, or UI component styles (디자인 시스템 토큰·스타일 가이드·컴포넌트 디자인).",
    "serena": "Deep AST repository-wide semantic exploration, code symbols, and architecture analysis (코드베이스 AST 심층 탐색·심볼 분석·아키텍처 구조).",
    "context7": "External 3rd-party library API documentation lookup and references (외부 라이브러리 및 API 공식 문서 검색·도큐먼트 참조).",
    "stitch": "Workflow stitching, multi-agent pipeline synthesis, and tool composition (워크플로우 합성·에이전트 파이프라인 조립).",
    "rea": "Reverse engineering, React component inspection, decompilation, and code reconstruction (리액트 역공학·컴포넌트 디컴파일 분석·코드 구조 복원).",
    "reticle": "Precise targeting, crosshair locator, screen element coordinate mapping, and visual inspection (레티클 좌표 조준·정밀 타겟팅·화면 클릭 위치 검사).",
    "playmcp-gateway": "KakaoTalk messaging, Naver search, external web portal integration and Korean services (카카오톡 메시지 전송·네이버 검색·외부 포털 서비스 연동).",
    "filesystem": "Local filesystem file exploration, directory tree navigation, reading and writing files (로컬 파일 시스템 탐색·폴더 트리·디렉토리 목록·파일 읽기 쓰기).",
    "playwright": "Browser automation, web scraping, navigating web pages, and taking screenshots (브라우저 자동화·웹 스크래핑·웹 페이지 탐색·스크린샷 촬영).",
    "memory": "Persistent knowledge graph, remembering user preferences and long-term facts (장기 기억·지식 그래프·사용자 정보 기억).",
    "github": "GitHub repository management, issues, PRs, and commit history (깃허브 레포지토리·풀리퀘스트 PR·이슈 관리).",
    "slack": "Slack messaging, channel notifications, and team chat integration (슬랙 메시지·채널 알림·팀 채팅 연동).",
    "postgres": "PostgreSQL database queries, schema inspection, and SQL execution (PostgreSQL 데이터베이스 쿼리·테이블 스키마).",
    "sqlite": "SQLite database inspection, table queries, and local data storage (SQLite 로컬 DB 쿼리·테이블 검사).",
}


def _get_dynamic_mcp_criteria(available_mcps: List[str], custom_descs: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Dynamically build criteria for ModernBERT choice evaluation."""
    criteria = {
        "none": "Standard coding, bug fixing, local file editing, script running. No external tool needed."
    }

    # Try reading data/mcp_servers.json labels if available
    mcp_labels: Dict[str, str] = {}
    try:
        cand_paths = [
            Path("data/mcp_servers.json"),
            Path(__file__).resolve().parent.parent / "data" / "mcp_servers.json",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "daon-agent-system" / "resources" / "data" / "mcp_servers.json",
            Path(os.environ.get("LOCALAPPDATA", "")) / "DAON Agent System" / "data" / "mcp_servers.json",
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

    custom = custom_descs or {}
    for mcp in available_mcps:
        if mcp in custom:
            criteria[mcp] = custom[mcp]
        elif mcp in KNOWN_MCP_CRITERIA:
            criteria[mcp] = KNOWN_MCP_CRITERIA[mcp]
        elif mcp in mcp_labels:
            criteria[mcp] = f"{mcp_labels[mcp]} ({mcp} tools and actions)."
        else:
            clean_name = mcp.replace("-", " ").replace("_", " ").title()
            criteria[mcp] = f"Specialized {clean_name} operations, external integration, and {mcp} tools."

    return criteria


class LayaRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler for Laya decision requests."""

    def _send_json(self, status: int, data: Dict[str, Any]):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            vram_mb = 0
            try:
                import torch
                if torch.cuda.is_available():
                    vram_mb = round(torch.cuda.memory_allocated(0) / (1024 * 1024), 2)
            except Exception:
                pass

            self._send_json(200, {
                "status": "ok" if _router is not None else ("booting" if not _ready else "degraded"),
                "model": MODEL_ID,
                "device": _device,
                "vram_allocated_mb": vram_mb,
                "uptime_seconds": round(time.time() - _start_time, 1)
            })
        else:
            self._send_json(404, {"error": "Not Found"})

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        raw_bytes = self.rfile.read(content_len) if content_len > 0 else b"{}"
        try:
            body = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                body = raw_bytes.decode("cp949")
            except UnicodeDecodeError:
                body = raw_bytes.decode("utf-8", errors="replace")

        try:
            req_data = json.loads(body)
        except Exception:
            self._send_json(400, {"error": "Invalid JSON"})
            return

        if self.path == "/prune_mcp":
            self._handle_prune_mcp(req_data)
        elif self.path == "/pre_route":
            self._handle_pre_route(req_data)
        elif self.path == "/decide":
            self._handle_decide(req_data)
        elif self.path == "/batch_classify":
            self._handle_batch_classify(req_data)
        else:
            self._send_json(404, {"error": "Endpoint not found"})

    def _handle_prune_mcp(self, req_data: Dict[str, Any]):
        prompt = req_data.get("prompt", "")
        available_mcps = req_data.get("available_mcps", [])
        mcp_descriptions = req_data.get("mcp_descriptions", {})
        if not prompt or not available_mcps:
            self._send_json(200, {"needed_mcps": []})
            return

        if _router is None:
            # Fallback if model not loaded
            self._send_json(200, {"needed_mcps": available_mcps, "fallback": True})
            return

        t0 = time.time()
        # Single-forward-pass choice question for ultra-fast evaluation
        criteria = _get_dynamic_mcp_criteria(available_mcps, mcp_descriptions)

        question = {
            "needed_tool": {
                "type": "choice",
                "instructions": "Which external MCP tool is required for this coding prompt?",
                "criteria": criteria
            }
        }

        needed = []
        try:
            state = {"prompt": prompt}
            preds = _router.predict(state, question)
            ans = preds.get("answers", {}).get("needed_tool", {})
            choice = ans.get("choice")
            probs = ans.get("probabilities", {})

            if choice and choice != "none" and choice in available_mcps:
                needed.append(choice)
                # Check if secondary MCP also has high probability (>= 0.30)
                for mcp_name, prob in probs.items():
                    if mcp_name != "none" and mcp_name in available_mcps and prob >= 0.30 and mcp_name not in needed:
                        needed.append(mcp_name)
            elif choice == "none":
                # If "none" is top choice, external tools are NOT needed unless another tool is closely tied (>= 0.35 and diff < 0.05)
                none_prob = probs.get("none", 0.5)
                for mcp_name, prob in probs.items():
                    if mcp_name != "none" and mcp_name in available_mcps and prob >= 0.35 and (none_prob - prob) < 0.05:
                        needed.append(mcp_name)
        except Exception as e:
            _logger.warning("Prune prediction failed: %s", e)
            needed = []

        latency_ms = round((time.time() - t0) * 1000, 1)
        _logger.info("Pruned MCPs in %.1fms: %s -> %s (choice: %s)", latency_ms, available_mcps, needed, choice)
        self._send_json(200, {
            "needed_mcps": needed,
            "choice": choice,
            "probabilities": probs,
            "latency_ms": latency_ms
        })

    def _handle_pre_route(self, req_data: Dict[str, Any]):
        """System 1 inline pre-routing for Raon (streaming agent).
        
        Classifies prompt intent in a single ultra-fast forward pass (~30ms on GPU).
        """
        prompt = req_data.get("prompt", "")
        if not prompt or _router is None:
            self._send_json(200, {
                "intent": "conversation",
                "confidence": 0.5,
                "latency_ms": 0.0,
                "fallback": True
            })
            return

        t0 = time.time()
        question = {
            "intent": {
                "type": "choice",
                "instructions": "Classify the user intent for the AI assistant",
                "criteria": {
                    "conversation": "General questions, greetings, chat, advice, asking explanations without wanting code or file modifications.",
                    "coding": "Editing local files, writing code, fixing bugs, running scripts, git commands, building or debugging.",
                    "worker_task": "Multi-step complex autonomous refactoring, heavy background tasks, repo-wide search requiring a subagent worker.",
                    "design_ui": "Frontend styling, CSS, UI tokens, Figma design cards, layout adjustments, themes."
                }
            }
        }
        try:
            state = {"prompt": prompt}
            preds = _router.predict(state, question)
            ans = preds.get("answers", {}).get("intent", {})
            choice = ans.get("choice", "coding")
            probs = ans.get("probabilities", {})
            conf = float(probs.get(choice, 0.5)) if isinstance(probs, dict) else 0.5
            elapsed = round((time.time() - t0) * 1000, 1)
            self._send_json(200, {
                "intent": choice,
                "confidence": round(conf, 4),
                "probabilities": probs,
                "latency_ms": elapsed
            })
        except Exception as e:
            _logger.warning("Laya pre_route prediction failed: %s", e)
            self._send_json(200, {
                "intent": "conversation",
                "confidence": 0.5,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "fallback": True
            })

    def _handle_decide(self, req_data: Dict[str, Any]):
        raw_state = req_data.get("state", {})
        raw_questions = req_data.get("questions", {})
        if _router is None:
            self._send_json(503, {"error": "Laya model not initialized"})
            return

        # ── Normalize state: ensure string or dict with text/prompt ──
        norm_state = {}
        if isinstance(raw_state, str):
            norm_state = {"text": raw_state}
        elif isinstance(raw_state, dict):
            norm_state = dict(raw_state)
            if not any(k in norm_state for k in ["text", "prompt"]):
                norm_state["text"] = " ".join(f"{k}: {v}" for k, v in norm_state.items()) or "None"
        else:
            norm_state = {"text": str(raw_state)}

        # ── Normalize questions: ensure required Laya schema (instructions, type, criteria) ──
        norm_questions = {}
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

        t0 = time.time()
        try:
            preds = _router.predict(norm_state, norm_questions)
            latency_ms = round((time.time() - t0) * 1000, 1)
            self._send_json(200, {
                "decisions": preds.get("answers", preds),
                "full_output": preds,
                "latency_ms": latency_ms
            })
        except Exception as e:
            _logger.error("Decide error: %s", e)
            self._send_json(500, {"error": str(e)})

    def _handle_batch_classify(self, req_data: Dict[str, Any]):
        items = req_data.get("items", [])
        categories = req_data.get("categories", {})
        instruction = req_data.get("instruction", "Classify this item into one category")

        if _router is None:
            self._send_json(503, {"error": "Laya model not initialized"})
            return

        if not items:
            self._send_json(200, {"results": [], "count": 0, "latency_ms": 0.0})
            return

        t0 = time.time()
        results = []
        probabilities = []
        question_def = {
            "type": "choice",
            "instructions": instruction or "Classify this item into one category",
            "criteria": categories
        }

        # True Batched Tensor Inference (16x speedup on CUDA)
        try:
            import numpy as np
            import torch
            from laya.agent import QTYPES, build_sequence, collate_items, render_options, temp_bucket

            agent = _router
            agent._check_question("category", question_def)
            q = agent._to_internal(question_def)
            max_len = agent.cfg.get("max_len", 512)
            head_max_len = agent.cfg.get("head_max_len", 192)
            keys = list(q["crit"].keys())
            qt = QTYPES[q["t"]]
            k = len(render_options(q))
            t_scale = agent.temperature_by_options.get(temp_bucket(qt, k), agent.temperature[qt])
            use_amp = agent.device.type == "cuda"
            batch_size = 32

            # Pre-build sequence items
            item_records = []
            for item in items:
                s, m = build_sequence(agent.tok, {"text": str(item)}, q, max_len, head_max_len)
                item_records.append({"ids": s, "markers": m, "qtype": qt})

            with torch.no_grad():
                for i in range(0, len(item_records), batch_size):
                    chunk = item_records[i : i + batch_size]
                    b = collate_items([chunk], agent.tok.pad_token_id)
                    with torch.autocast(device_type=agent.device.type, dtype=agent.dtype, enabled=use_amp):
                        logits, act = agent.model(
                            b["input_ids"].to(agent.device),
                            b["attention_mask"].to(agent.device),
                            b["marker_pos"].to(agent.device),
                            b["marker_mask"].to(agent.device),
                            b["qtype"].to(agent.device),
                        )
                    logits_np = logits.float().cpu().numpy()[:, :k] / t_scale
                    p = np.exp(logits_np - np.max(logits_np, axis=-1, keepdims=True))
                    p = p / np.sum(p, axis=-1, keepdims=True)
                    for row_idx, choice_idx in enumerate(p.argmax(axis=-1)):
                        results.append(keys[choice_idx])
                        probabilities.append({keys[c_idx]: round(float(p[row_idx, c_idx]), 4) for c_idx in range(k)})

        except Exception as e:
            _logger.warning("True batch classify failed, falling back to sequential predict: %s", e)
            results = []
            probabilities = []
            question = {"category": question_def}
            for item in items:
                try:
                    pred = _router.predict({"text": str(item)}, question)
                    ans = pred.get("answers", {}).get("category", {})
                    choice_val = ans.get("choice") if isinstance(ans, dict) else ans
                    results.append(choice_val)
                    if isinstance(ans, dict) and "probabilities" in ans:
                        probabilities.append(ans["probabilities"])
                    else:
                        probabilities.append({})
                except Exception:
                    results.append(None)
                    probabilities.append({})

        latency_ms = round((time.time() - t0) * 1000, 1)
        self._send_json(200, {
            "results": results,
            "probabilities": probabilities,
            "count": len(results),
            "latency_ms": latency_ms
        })

    def log_message(self, format, *args):
        # Mute standard noisy HTTP log lines
        pass


class LayaHTTPServer(ThreadingHTTPServer):
    """[FIX 2026-10-01] Single-instance lock.

    On Windows, SO_REUSEADDR lets a SECOND process bind the SAME port
    (similar to Linux SO_REUSEPORT), so two Laya daemons could coexist and
    each hold ~1.85GB VRAM. Setting it False makes the second bind fail with
    WinError 10048 -> the bind itself becomes the single-instance lock.
    """
    allow_reuse_address = False


def main():
    global PORT
    # [FIX 2026-10-01] Honour --port instead of silently ignoring it (the
    # parents already pass --port 8765). Also lets us test the single-instance
    # lock on a throwaway port without touching the live daemon.
    try:
        import argparse
        _ap = argparse.ArgumentParser(description="DAON Laya Decision Daemon")
        _ap.add_argument("--port", type=int, default=PORT)
        PORT = _ap.parse_args().port
    except SystemExit:
        raise
    print(f"=== DAON Laya Decision Daemon ===", flush=True)
    # [FIX 2026-10-01] Bind the socket BEFORE loading the model.
    # Old order: load model (30~60s) -> bind. During startup /health was
    # unresponsive, so BOTH parents (Electron ServerSupervisor and
    # server.exe) independently concluded "no Laya" and started a duplicate
    # -> two daemons, ~1.85GB VRAM wasted.
    # New order: bind first (second instance exits immediately), then load the
    # model in a background thread so /health answers 200(status=booting)
    # right away and the parents never respawn.
    try:
        server = LayaHTTPServer(("127.0.0.1", PORT), LayaRequestHandler)
    except OSError as e:
        print(f"[LayaService] Port {PORT} already in use - another Laya instance is running. Exiting. ({e})", flush=True)
        return
    print(f"[LayaService] Listening on http://127.0.0.1:{PORT} (model loading in background)", flush=True)
    threading.Thread(target=init_laya_model, name="laya-model-loader", daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[LayaService] Stopping server...", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
