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
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            req_data = json.loads(body)
        except Exception:
            self._send_json(400, {"error": "Invalid JSON"})
            return

        if self.path == "/prune_mcp":
            self._handle_prune_mcp(req_data)
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

    def _handle_decide(self, req_data: Dict[str, Any]):
        state = req_data.get("state", {})
        questions = req_data.get("questions", {})
        if _router is None:
            self._send_json(503, {"error": "Laya model not initialized"})
            return

        t0 = time.time()
        try:
            preds = _router.predict(state, questions)
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

        t0 = time.time()
        results = []
        question = {
            "category": {
                "type": "choice",
                "instructions": instruction,
                "criteria": categories
            }
        }
        for item in items:
            try:
                pred = _router.predict({"text": str(item)}, question)
                ans = pred.get("answers", {}).get("category", {})
                choice_val = ans.get("choice") if isinstance(ans, dict) else ans
                results.append(choice_val)
            except Exception as e:
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
