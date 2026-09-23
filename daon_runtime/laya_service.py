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


def init_laya_model():
    """Initialize Laya model on GPU (if available) or CPU."""
    global _router, _device
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
        import laya
        t0 = time.time()
        _router = laya.load(MODEL_ID)
        _logger.info("Laya model loaded successfully in %.2f seconds on %s", time.time() - t0, _device)
    except Exception as e:
        _logger.error("Failed to load Laya model: %s", e, exc_info=True)
        _router = None


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
                "status": "ok" if _router is not None else "degraded",
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
        if not prompt or not available_mcps:
            self._send_json(200, {"needed_mcps": []})
            return

        if _router is None:
            # Fallback if model not loaded
            self._send_json(200, {"needed_mcps": available_mcps, "fallback": True})
            return

        t0 = time.time()
        # Single-forward-pass choice question for ultra-fast evaluation
        criteria = {
            "none": "Standard coding, bug fixing, local file editing, script running. No external tool needed."
        }
        if "figma" in available_mcps:
            criteria["figma"] = "Figma design frames, component styles, or UI design tokens."
        if "daon-design" in available_mcps:
            criteria["daon-design"] = "DAON UI design system style cards, color tokens, or UI component styles."
        if "serena" in available_mcps:
            criteria["serena"] = "Deep AST repository-wide semantic exploration."
        if "context7" in available_mcps:
            criteria["context7"] = "External 3rd-party library API documentation lookup."
        if "stitch" in available_mcps:
            criteria["stitch"] = "Workflow stitching and synthesis."

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

            # Check if secondary MCP has high probability (> 0.25)
            for mcp_name, prob in probs.items():
                if mcp_name != "none" and mcp_name in available_mcps and prob >= 0.25 and mcp_name not in needed:
                    needed.append(mcp_name)
        except Exception as e:
            _logger.warning("Prune prediction failed: %s", e)
            needed = []

        latency_ms = round((time.time() - t0) * 1000, 1)
        _logger.info("Pruned MCPs in %.1fms: %s -> %s", latency_ms, available_mcps, needed)
        self._send_json(200, {
            "needed_mcps": needed,
            "latency_ms": latency_ms
        })

    def _handle_pre_route(self, req_data: Dict[str, Any]):
        """System 1 inline pre-routing for Raon (streaming agent).
        
        Classifies prompt intent in a single ultra-fast forward pass (~30ms on GPU).
        """
        prompt = req_data.get("prompt", "")
        if not prompt or _router is None:
            self._send_json(200, {
                "intent": "general",
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
                "intent": "general",
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
                    for choice_idx in p.argmax(axis=-1):
                        results.append(keys[choice_idx])

        except Exception as e:
            _logger.warning("True batch classify failed, falling back to sequential predict: %s", e)
            results = []
            question = {"category": question_def}
            for item in items:
                try:
                    pred = _router.predict({"text": str(item)}, question)
                    ans = pred.get("answers", {}).get("category", {})
                    choice_val = ans.get("choice") if isinstance(ans, dict) else ans
                    results.append(choice_val)
                except Exception:
                    results.append(None)

        latency_ms = round((time.time() - t0) * 1000, 1)
        self._send_json(200, {"results": results, "count": len(results), "latency_ms": latency_ms})

    def log_message(self, format, *args):
        # Mute standard noisy HTTP log lines
        pass


def main():
    print(f"=== DAON Laya Decision Daemon ===", flush=True)
    init_laya_model()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), LayaRequestHandler)
    print(f"[LayaService] Listening on http://127.0.0.1:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[LayaService] Stopping server...", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
