"""
Dashboard API — Token usage, cost, and execution metrics aggregation.

Provides:
- GET /api/dashboard/metrics — Aggregated token/cost/execution stats from
  dynamic runs (metrics.json) and chat sessions (data/sessions/*.json).
"""

import json
from pathlib import Path
from datetime import datetime

from api.helpers import j, bad
from api.config import get_config

# ── Model pricing table (USD per 1M tokens) ──
# Keys: lowercase model identifier substrings (matched with `in`)
# Values: (input_price_per_1M, output_price_per_1M)
_MODEL_PRICING = {
    # OpenAI
    "gpt-5.4": (2.50, 10.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.5": (75.00, 150.00),
    "o4-mini": (1.10, 4.40),
    "o3": (10.00, 40.00),
    "o3-mini": (1.10, 4.40),
    "o1": (15.00, 60.00),
    # Anthropic
    "claude-sonnet-4.6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),
    "claude-opus-4.5": (15.00, 75.00),
    "claude-haiku-4.5": (0.80, 4.00),
    "claude-haiku-3-5": (0.80, 4.00),
    "claude-3.5-sonnet": (3.00, 15.00),
    "claude-3.5-haiku": (0.80, 4.00),
    # Google
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-3-flash": (0.15, 0.60),
    "gemini-3.1-flash": (0.10, 0.40),
    "gemini-3": (0.10, 0.40),
    "gemini-2.0-flash": (0.10, 0.40),
    # DeepSeek
    "deepseek-chat-v3": (0.27, 1.10),
    "deepseek-r1": (0.55, 2.19),
    "deepseek-v3": (0.27, 1.10),
    "deepseek": (0.27, 1.10),
    # Meta
    "llama-4": (0.25, 1.25),
    "llama-3.3": (0.10, 0.40),
    "llama-3.1": (0.15, 0.60),
    # MiniMax
    "minimax": (0.20, 1.10),
    # Grok
    "grok": (2.00, 8.00),
    # Mistral
    "mistral": (0.20, 0.60),
    # Cohere
    "command": (0.50, 1.50),
}


def _estimate_model_cost(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0) -> dict:
    """Estimate USD cost for a model based on token usage."""
    model_lower = model.lower()
    input_price = 0.0
    output_price = 0.0

    for key, (inp, out) in _MODEL_PRICING.items():
        if key in model_lower:
            input_price = inp
            output_price = out
            break

    input_cost = (input_tokens / 1_000_000) * input_price
    output_cost = (output_tokens / 1_000_000) * output_price

    return {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "input_price_per_1M": input_price,
        "output_price_per_1M": output_price,
        "estimated_cost": round(input_cost + output_cost, 6),
        "pricing_known": input_price > 0 or output_price > 0,
    }


def _get_sessions_dir() -> Path:
    cfg = get_config()
    data_dir = Path(cfg.get("data_dir", "data"))
    return data_dir / "sessions"


def _get_dynamic_runs_dir() -> Path:
    """Find all dynamic run directories containing metrics.json."""
    # Try common locations
    candidates = [
        Path("_workspace/dynamic_runs"),
        Path("../_workspace/dynamic_runs"),
        Path.cwd() / "_workspace" / "dynamic_runs",
    ]
    # Also check if sys._MEIPASS exists (PyInstaller)
    import sys
    if hasattr(sys, '_MEIPASS'):
        candidates.insert(0, Path(sys.executable).parent.parent / "_workspace" / "dynamic_runs")

    for c in candidates:
        if c.exists():
            return c
    return None


def handle_get_dashboard_metrics(handler, parsed) -> bool:
    """
    GET /api/dashboard/metrics

    Returns aggregated metrics:
    - total: overall token/cost summary
    - by_model: breakdown per model
    - by_agent: breakdown per agent (from dynamic runs)
    - recent_runs: list of recent dynamic runs with metrics
    - session_usage: total token usage from chat sessions
    """
    result = {
        "total": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "estimated_cost": 0.0,
            "total_runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "total_session_tokens": 0,
        },
        "by_model": {},       # model_name -> token/cost aggregates
        "by_agent": {},       # agent_name -> token/cost aggregates
        "recent_runs": [],    # last 20 dynamic runs
        "session_usage": {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_estimated_cost": 0.0,
            "session_count": 0,
        },
        "models_used": [],    # list of distinct model names
        "agents_used": [],    # list of distinct agent names
    }

    # ── 1. Aggregate from dynamic runs (metrics.json) ──
    runs_dir = _get_dynamic_runs_dir()
    run_entries = []

    if runs_dir and runs_dir.exists():
        for run_path in sorted(runs_dir.iterdir(), reverse=True):
            if not run_path.is_dir():
                continue
            metrics_file = run_path / "metrics.json"
            if not metrics_file.exists():
                continue

            try:
                metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            status = metrics.get("status", "unknown")
            result["total"]["total_runs"] += 1
            if status == "success":
                result["total"]["successful_runs"] += 1
            else:
                result["total"]["failed_runs"] += 1

            nodes = metrics.get("nodes", {})
            run_input = 0
            run_output = 0
            run_cache = 0
            run_cost = 0.0

            for node_name, node_data in nodes.items():
                model = node_data.get("model_used", "unknown")
                inp = node_data.get("input_tokens", 0) or 0
                out = node_data.get("output_tokens", 0) or 0
                cache_r = node_data.get("cache_read_tokens", 0) or 0

                run_input += inp
                run_output += out
                run_cache += cache_r

                # Per-model aggregation
                if model not in result["by_model"]:
                    result["by_model"][model] = {
                        "input_tokens": 0, "output_tokens": 0,
                        "cache_read_tokens": 0, "estimated_cost": 0.0,
                        "run_count": 0, "node_count": 0,
                    }
                result["by_model"][model]["input_tokens"] += inp
                result["by_model"][model]["output_tokens"] += out
                result["by_model"][model]["cache_read_tokens"] += cache_r
                result["by_model"][model]["node_count"] += 1

                # Per-agent aggregation (use agent name as key)
                if node_name not in result["by_agent"]:
                    result["by_agent"][node_name] = {
                        "input_tokens": 0, "output_tokens": 0,
                        "cache_read_tokens": 0, "estimated_cost": 0.0,
                        "run_count": 0, "model": model,
                    }
                result["by_agent"][node_name]["input_tokens"] += inp
                result["by_agent"][node_name]["output_tokens"] += out
                result["by_agent"][node_name]["cache_read_tokens"] += cache_r

                # Estimate cost for this node
                cost_info = _estimate_model_cost(model, inp, out, cache_r)
                run_cost += cost_info["estimated_cost"]

                # Update agent cost
                result["by_agent"][node_name]["estimated_cost"] += cost_info["estimated_cost"]

            # Update model run_count and cost
            for model in set(
                node_data.get("model_used", "unknown")
                for node_data in nodes.values()
            ):
                if model in result["by_model"]:
                    result["by_model"][model]["run_count"] += 1

            # Compute cost per model
            for model, data in result["by_model"].items():
                cost_info = _estimate_model_cost(
                    model, data["input_tokens"], data["output_tokens"],
                    data["cache_read_tokens"]
                )
                data["estimated_cost"] = cost_info["estimated_cost"]

            result["total"]["input_tokens"] += run_input
            result["total"]["output_tokens"] += run_output
            result["total"]["cache_read_tokens"] += run_cache
            result["total"]["estimated_cost"] += run_cost

            # Add to recent runs (limit 20)
            if len(run_entries) < 20:
                run_entries.append({
                    "run_id": run_path.name,
                    "task": metrics.get("task", "")[:120],
                    "status": status,
                    "start_time": metrics.get("start_time", 0),
                    "end_time": metrics.get("end_time", 0),
                    "total_wall_time": metrics.get("total_wall_time", 0),
                    "input_tokens": run_input,
                    "output_tokens": run_output,
                    "estimated_cost": round(run_cost, 6),
                    "node_count": len(nodes),
                    "error": metrics.get("error", "")[:200],
                })

    result["recent_runs"] = run_entries

    # ── 2. Aggregate from chat sessions ──
    sessions_dir = _get_sessions_dir()
    if sessions_dir and sessions_dir.exists():
        session_count = 0
        session_input = 0
        session_output = 0
        session_cost = 0.0

        for session_file in sessions_dir.glob("*.json"):
            if session_file.name.startswith("_"):
                continue
            try:
                session = json.loads(session_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            inp = session.get("input_tokens", 0) or 0
            out = session.get("output_tokens", 0) or 0
            cost = session.get("estimated_cost", 0) or 0

            if inp == 0 and out == 0:
                continue

            session_count += 1
            session_input += inp
            session_output += out

            # Parse cost if it's a string
            if isinstance(cost, str):
                try:
                    session_cost += float(cost)
                except (ValueError, TypeError):
                    pass
            else:
                try:
                    session_cost += float(cost)
                except (ValueError, TypeError):
                    pass

        result["session_usage"] = {
            "total_input_tokens": session_input,
            "total_output_tokens": session_output,
            "total_estimated_cost": round(session_cost, 6),
            "session_count": session_count,
        }
        result["total"]["total_session_tokens"] = session_input + session_output

    # ── 3. Build distinct lists ──
    result["models_used"] = sorted(result["by_model"].keys())
    result["agents_used"] = sorted(result["by_agent"].keys())

    # Round top-level totals
    result["total"]["estimated_cost"] = round(result["total"]["estimated_cost"], 6)

    return j(handler, result)
