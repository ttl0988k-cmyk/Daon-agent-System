#!/usr/bin/env python3
"""
Daon Design MCP Server — exposes the Daon Design System (DesignGraph + StyleMixer
+ ComponentRetriever + StyleCardRegistry + StyleCardRetriever) as MCP tools so
that AI agents can search, mix, audit, and orchestrate design components directly.

Protocol: JSON-RPC 2.0 over stdio (newline-delimited), MCP 2024-11-05.

Usage:
    python api/mcp/daon_design_mcp.py          # run from project root
    python -m api.mcp.daon_design_mcp          # as module

Environment variables (optional):
    FIGMA_API_KEY   — if set, enables figma_import_style_card tool
"""

import sys
import os
import json
import threading
import traceback
import logging
from pathlib import Path
from typing import Optional

# ── Bootstrap project path (so imports work from anywhere) ────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # api/mcp/ -> api/ -> root
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── Logging (stderr only, since stdout is the MCP transport) ──────────────
# Redirect ALL Python loggers to stderr so nothing contaminates the JSON-RPC stdout pipe.
_root_logger = logging.getLogger()
_root_logger.handlers.clear()
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setFormatter(logging.Formatter(
    "[%(name)s] %(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
))
_root_logger.addHandler(_stderr_handler)
_root_logger.setLevel(logging.INFO)

_logger = logging.getLogger("daon-design-mcp")

# Windows UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Lazy imports ──────────────────────────────────────────────────────────
_registry = None
_design_graph = None
_style_mixer = None
_component_retriever = None
_style_card_retriever = None
_card_registry = None


def _get_registry():
    global _registry
    if _registry is None:
        from api.style_card import get_style_card_registry
        _registry = get_style_card_registry()
        if _registry.card_count == 0:
            _registry.load_all()
    return _registry


def _get_design_graph():
    global _design_graph
    if _design_graph is None:
        from api.style_card import get_design_graph
        _design_graph = get_design_graph()
        if _design_graph.component_count == 0:
            reg = _get_registry()
            if reg.card_count > 0:
                _design_graph.ingest_from_registry(reg)
    return _design_graph


def _get_style_mixer():
    global _style_mixer
    if _style_mixer is None:
        from api.dynamic.style_mixer import get_style_mixer
        _style_mixer = get_style_mixer()
    return _style_mixer


def _get_component_retriever():
    global _component_retriever
    if _component_retriever is None:
        from api.dynamic.component_retriever import get_component_retriever
        _component_retriever = get_component_retriever()
    # Always check and rebuild if index is missing (new process, fresh singleton)
    dg = _get_design_graph()
    if dg.component_count > 0:
        if _component_retriever._index is None or len(_component_retriever._index) == 0:
            _component_retriever.rebuild_index(dg)
    return _component_retriever


def _get_style_card_retriever():
    global _style_card_retriever
    if _style_card_retriever is None:
        from api.dynamic.style_card_retriever import get_style_card_retriever
        _style_card_retriever = get_style_card_retriever()
        reg = _get_registry()
        if reg.card_count > 0:
            _style_card_retriever.rebuild_index(reg)
    return _style_card_retriever


# ── Tool implementations ──────────────────────────────────────────────────

def tool_design_search_cards(args: dict) -> dict:
    """Search Style Cards by keyword query or tags."""
    query = args.get("query", "")
    tags = args.get("tags", [])
    category = args.get("category")
    top_k = int(args.get("top_k", 10))

    reg = _get_registry()
    results = []

    if tags:
        cards = reg.search_by_tags(tags)
    elif query:
        retriever = _get_style_card_retriever()
        cards_with_scores = retriever.retrieve(query, top_k=top_k)
        cards = [c for c, _ in cards_with_scores]
    else:
        # Return all cards grouped by category
        if category:
            cards = reg.get_by_category(category)
        else:
            cards = list(reg._cards.values())

    for card in cards[:top_k]:
        d = card.to_dict()
        # Trim large fields for MCP response efficiency
        d.pop("design_dna", None)
        d.pop("evaluation_notes", None)
        d.pop("decomposed_cards", None)
        results.append(d)

    return {
        "total": len(results),
        "cards": results,
    }


def tool_design_get_card(args: dict) -> dict:
    """Get a full Style Card by ID."""
    card_id = args.get("card_id", "")
    if not card_id:
        return {"error": "card_id is required"}

    reg = _get_registry()
    card = reg.get(card_id)
    if card is None:
        return {"error": f"Style Card not found: {card_id}"}

    return {"card": card.to_dict()}


def tool_design_list_categories(args: dict) -> dict:
    """List all design categories and their statistics."""
    reg = _get_registry()
    dg = _get_design_graph()

    return {
        "style_card_categories": reg.get_all_categories(),
        "component_categories": dg.get_all_categories(),
        "component_summary": dg.get_category_summary(),
        "total_style_cards": reg.card_count,
        "total_components": dg.component_count,
    }


def tool_design_search_components(args: dict) -> dict:
    """Search ComponentCards by intent query with optional category filter."""
    query = args.get("query", "")
    category = args.get("category")
    sub_category = args.get("sub_category")
    top_k = int(args.get("top_k", 5))
    min_score = float(args.get("min_score", 0.0))

    if not query:
        return {"error": "query is required"}

    retriever = _get_component_retriever()
    matches = retriever.retrieve(
        query,
        top_k=top_k,
        category_filter=category,
        sub_category_filter=sub_category,
        min_score=min_score,
    )

    results = []
    for comp, score in matches:
        d = comp.to_dict()
        d["similarity_score"] = score
        d["style_hint"] = comp.to_brief_text()
        results.append(d)

    return {
        "query": query,
        "matches": len(results),
        "components": results,
    }


def tool_design_get_component_mix(args: dict) -> dict:
    """Get the best component from each category for Style Mixing.

    This is the core Style Mixing operation: retrieves top-N ComponentCards
    per category, ready for harmony validation and orchestration.
    """
    query = args.get("query", "")
    categories = args.get("categories")  # optional list, defaults to all
    top_n = int(args.get("top_n", 3))
    min_score = float(args.get("min_score", 0.0))

    if not query:
        return {"error": "query is required"}

    retriever = _get_component_retriever()
    mix = retriever.retrieve_component_mix(
        query,
        categories=categories,
        top_n=top_n,
        min_score=min_score,
    )

    # Format for MCP response (tuples -> dicts)
    formatted_mix = {}
    for cat, entries in mix.items():
        formatted_mix[cat] = []
        for comp, score in entries:
            formatted_mix[cat].append({
                "id": comp.id,
                "name": comp.name,
                "category": comp.category,
                "sub_category": comp.sub_category,
                "similarity_score": score,
                "brief": comp.to_brief_text()[:300],
                "source_name": comp.source_name,
                "source_url": comp.source_url,
            })

    # Also get the full mix context (markdown)
    mix_context = retriever.to_mix_context(mix, max_per_category=1)
    compact_hints = retriever.to_compact_hints(mix)

    return {
        "query": query,
        "categories_found": list(formatted_mix.keys()),
        "total_components": sum(len(v) for v in formatted_mix.values()),
        "mix": formatted_mix,
        "mix_context_markdown": mix_context,
        "compact_hints": compact_hints,
    }


def tool_design_orchestrate(args: dict) -> dict:
    """Run the full StyleMixer orchestration pipeline.

    Chains: validate_harmony → resolve_conflicts → check_trends →
            check_accessibility → build_unified_brief

    This is the all-in-one, single-call design orchestration tool.
    """
    intent_summary = args.get("intent_summary", "")
    categories = args.get("categories")  # optional
    top_n = int(args.get("top_n", 3))

    if not intent_summary:
        return {"error": "intent_summary is required (describe the design intent)"}

    # Step 1: Retrieve component mix
    retriever = _get_component_retriever()
    component_mix = retriever.retrieve_component_mix(
        intent_summary, categories=categories, top_n=top_n
    )

    if not component_mix:
        return {"error": "No matching components found for this intent. Try different keywords."}

    # Step 2: Run orchestration
    mixer = _get_style_mixer()
    result = mixer.orchestrate(intent_summary, component_mix)

    # Serialize reports to dicts (they contain dataclass instances)
    trend_warnings = [
        w for w in result["trend_report"].warnings
    ] if result["trend_report"] else []

    a11y_warnings = [
        {"component": w.get("component", ""), "issue": w.get("issue", ""), "severity": w.get("severity", "warning")}
        for w in (result.get("a11y_warnings", []) or [])
    ] if "a11y_warnings" in result else []

    return {
        "intent": intent_summary,
        "brief": result["brief"],
        "compact_brief": result["compact_brief"],
        "harmony_score": result["harmony_score"],
        "trend_score": result["trend_score"],
        "a11y_score": result["a11y_score"],
        "conflicts_resolved": result["conflicts_resolved"],
        "component_count": result["component_count"],
        "trend_warnings": trend_warnings,
        "a11y_warnings": a11y_warnings,
    }


def tool_design_check_trends(args: dict) -> dict:
    """Check a component mix for deprecated patterns and current trends.

    Detects deprecated patterns (skeuomorphism, marquee, flash-intro, etc.)
    and confirms current trends (glassmorphism, dark-mode, micro-interactions).
    """
    query = args.get("query", "")
    categories = args.get("categories")

    if not query:
        return {"error": "query is required"}

    retriever = _get_component_retriever()
    component_mix = retriever.retrieve_component_mix(query, categories=categories)

    if not component_mix:
        return {"error": "No matching components found"}

    mixer = _get_style_mixer()
    report = mixer.check_trends(component_mix)

    return {
        "query": query,
        "deprecated_patterns": [
            {"pattern": w.get("pattern", ""), "found_in": w.get("found_in", ""), "advice": w.get("advice", "")}
            for w in report.deprecated
        ],
        "current_patterns": [
            {"pattern": w.get("pattern", ""), "found_in": w.get("found_in", ""), "note": w.get("note", "")}
            for w in report.current
        ],
        "trend_score": report.trend_score,
        "deprecated_count": len(report.deprecated),
        "current_count": len(report.current),
    }


def tool_design_check_accessibility(args: dict) -> dict:
    """Audit a component mix for WCAG accessibility compliance.

    Checks contrast ratio (WCAG AA ≥ 4.5:1), font size minimums,
    motion safety (prefers-reduced-motion), and overall accessibility score.
    """
    query = args.get("query", "")
    categories = args.get("categories")

    if not query:
        return {"error": "query is required"}

    retriever = _get_component_retriever()
    component_mix = retriever.retrieve_component_mix(query, categories=categories)

    if not component_mix:
        return {"error": "No matching components found"}

    mixer = _get_style_mixer()
    report = mixer.check_accessibility(component_mix)

    return {
        "query": query,
        "accessibility_score": report.accessibility_score,
        "passes": report.passes,
        "violations": [
            {"component": v.get("component", ""), "issue": v.get("issue", ""), "severity": v.get("severity", "warning")}
            for v in report.violations
        ],
        "violation_count": len(report.violations),
    }


def tool_design_get_graph_summary(args: dict) -> dict:
    """Get DesignGraph statistics and category breakdown."""
    dg = _get_design_graph()

    summary = dg.get_category_summary()
    # Sort by total descending
    sorted_summary = dict(
        sorted(summary.items(), key=lambda x: x[1].get("total", 0), reverse=True)
    )

    return {
        "total_components": dg.component_count,
        "total_categories": len(dg.get_all_categories()),
        "category_breakdown": sorted_summary,
        "all_categories": dg.get_all_categories(),
    }


def tool_design_get_style_context(args: dict) -> dict:
    """Get design DNA context for Creative Director injection.

    Retrieves the most relevant Style Cards for a query and returns
    compact design hints suitable for prompt injection.
    """
    query = args.get("query", "")
    top_k = int(args.get("top_k", 5))

    if not query:
        return {"error": "query is required"}

    retriever = _get_style_card_retriever()

    # Get markdown context for Creative Director
    brief_context = retriever.retrieve_for_brief(query, top_k=top_k)
    dna_context = retriever.retrieve_design_dna_context(query, top_k=min(top_k, 3))

    # Also get raw cards
    cards_with_scores = retriever.retrieve(query, top_k=top_k)

    cards = []
    for card, score in cards_with_scores:
        cards.append({
            "id": card.id,
            "name": card.name,
            "category": card.category,
            "tags": card.tags,
            "similarity_score": round(score, 4),
            "style_hint": card.to_inline_style_hint(),
        })

    return {
        "query": query,
        "cards": cards,
        "brief_context_markdown": brief_context,
        "dna_context": dna_context,
    }


# ── Tool registry ─────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "design_search_cards",
        "description": "Search the Daon Style Card reference library by keyword query or tags. Returns matching design references with their style hints, categories, and metadata. Use this to discover existing design patterns before creating new ones.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language design intent or keywords (e.g., 'glassmorphism dark dashboard', 'minimalist landing page')."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Filter by exact tags (AND match). If provided, overrides query-based search."},
                "category": {"type": "string", "description": "Optional category filter (e.g., 'landing-page', 'dashboard', 'typography')."},
                "top_k": {"type": "integer", "description": "Maximum results to return (default: 10)."},
            },
            "required": [],
        },
    },
    {
        "name": "design_get_card",
        "description": "Retrieve a specific Style Card by its ID, including full design DNA, component decomposition, and evaluation data. Use this to deeply inspect a reference design.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "card_id": {"type": "string", "description": "The Style Card ID (e.g., 'stripe-dashboard', 'linear-app')."},
            },
            "required": ["card_id"],
        },
    },
    {
        "name": "design_list_categories",
        "description": "List all design categories available in the Style Card registry and DesignGraph, with component counts per category. Use this to understand what design knowledge is indexed.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "design_search_components",
        "description": "Search individual ComponentCards (hero, navbar, cta, footer, etc.) by intent query with optional category/sub-category filters. Returns scored matches with style hints. Use this for granular component-level search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Design intent keywords (e.g., 'glassmorphism hero section')."},
                "category": {"type": "string", "description": "Filter by component category (e.g., 'hero', 'cta', 'navbar', 'footer')."},
                "sub_category": {"type": "string", "description": "Further filter by sub-category."},
                "top_k": {"type": "integer", "description": "Maximum results (default: 5)."},
                "min_score": {"type": "number", "description": "Minimum similarity score 0.0-1.0 (default: 0.0)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "design_get_component_mix",
        "description": "Retrieve the best-matching ComponentCard from each design category (hero, navbar, cta, footer, features, pricing, etc.) for a given design intent. This is the core Style Mixing operation — different components from different reference sources are mixed into a unified design. Returns both structured data and markdown context blocks for Creative Director prompt injection.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Design intent description (e.g., 'saas dashboard with glassmorphism and dark mode')."},
                "categories": {"type": "array", "items": {"type": "string"}, "description": "Optional list of categories to include. Defaults to all available: hero, navbar, cta, footer, features, pricing, testimonial, contact, card, gallery, modal, sidebar, form."},
                "top_n": {"type": "integer", "description": "Top results per category (default: 3)."},
                "min_score": {"type": "number", "description": "Minimum similarity threshold (default: 0.0)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "design_orchestrate",
        "description": "Run the FULL design orchestration pipeline in a single call. This chains: (1) component retrieval per category, (2) harmony validation, (3) conflict resolution, (4) trend analysis (detects deprecated patterns like skeuomorphism, marquee and confirms current trends like glassmorphism, dark-mode), (5) accessibility audit (WCAG contrast, font size, motion safety), (6) unified design brief generation. Returns a complete design specification with scores for harmony, trends, and accessibility.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "intent_summary": {"type": "string", "description": "Detailed design intent description (e.g., 'A modern SaaS analytics dashboard with glassmorphism cards, dark mode, large data visualizations, and micro-interactions on hover')."},
                "categories": {"type": "array", "items": {"type": "string"}, "description": "Optional category list to focus the mix."},
                "top_n": {"type": "integer", "description": "Top components per category (default: 3)."},
            },
            "required": ["intent_summary"],
        },
    },
    {
        "name": "design_check_trends",
        "description": "Check a component mix against current design trends. Detects DEPRECATED patterns (skeuomorphism, marquee, flash-intro, heavy-shadow, table-layout, text-heavy) and confirms CURRENT trends (glassmorphism, dark-mode, variable-font, micro-interaction). Returns a trend score 0.0-1.0.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Design intent to retrieve components for trend checking."},
                "categories": {"type": "array", "items": {"type": "string"}, "description": "Optional category filter."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "design_check_accessibility",
        "description": "Audit a component mix for WCAG 2.1 AA accessibility compliance. Checks: contrast ratio (≥ 4.5:1 for normal text), minimum font size (≥ 12px), motion safety (duration ≤ 500ms if intensity ≥ 8), and overall accessibility evaluation score. Returns pass/fail per violation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Design intent to retrieve components for accessibility audit."},
                "categories": {"type": "array", "items": {"type": "string"}, "description": "Optional category filter."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "design_get_graph_summary",
        "description": "Get DesignGraph statistics: total components, total categories, and a breakdown of component counts per category with sub-category details. Use this to understand the scale and coverage of the design knowledge graph.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "design_get_style_context",
        "description": "Retrieve the most relevant Style Cards for a design query, returning compact design DNA hints and markdown context blocks suitable for injection into a Creative Director prompt. Use this when you need design reference context to guide an agent's creative output.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Design intent or keywords."},
                "top_k": {"type": "integer", "description": "Maximum results (default: 5)."},
            },
            "required": ["query"],
        },
    },
]

TOOL_MAP = {
    "design_search_cards": tool_design_search_cards,
    "design_get_card": tool_design_get_card,
    "design_list_categories": tool_design_list_categories,
    "design_search_components": tool_design_search_components,
    "design_get_component_mix": tool_design_get_component_mix,
    "design_orchestrate": tool_design_orchestrate,
    "design_check_trends": tool_design_check_trends,
    "design_check_accessibility": tool_design_check_accessibility,
    "design_get_graph_summary": tool_design_get_graph_summary,
    "design_get_style_context": tool_design_get_style_context,
}


# ── MCP JSON-RPC 2.0 over stdio ───────────────────────────────────────────

class DaonDesignMCPServer:
    """Minimal MCP server: JSON-RPC 2.0, newline-delimited, over stdio."""

    def __init__(self):
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._running = False

    def _gen_id(self) -> int:
        with self._id_lock:
            self._next_id += 1
            return self._next_id

    def send_response(self, rid, result):
        """Send a JSON-RPC success response."""
        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": rid,
            "result": result,
        }, ensure_ascii=False)
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()
        _logger.debug("→ response id=%s", rid)

    def send_error(self, rid, code: int, message: str):
        """Send a JSON-RPC error response."""
        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": rid,
            "error": {"code": code, "message": message},
        }, ensure_ascii=False)
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()
        _logger.debug("→ error id=%s: %s", rid, message)

    def handle_request(self, request: dict):
        """Process a single JSON-RPC request."""
        rid = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        _logger.info("← %s (id=%s)", method, rid)

        # ── initialize ────────────────────────────────────────────────
        if method == "initialize":
            return self.send_response(rid, {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": "Daon Design MCP",
                    "version": "1.0.0",
                },
            })

        # ── notifications/initialized ─────────────────────────────────
        if method == "notifications/initialized":
            _logger.info("Client initialized — ready for tool calls")
            return  # no response for notifications

        # ── tools/list ────────────────────────────────────────────────
        if method == "tools/list":
            return self.send_response(rid, {"tools": TOOLS})

        # ── tools/call ────────────────────────────────────────────────
        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            tool_fn = TOOL_MAP.get(tool_name)
            if tool_fn is None:
                return self.send_error(rid, -32601, f"Unknown tool: {tool_name}")

            try:
                result = tool_fn(arguments)
                return self.send_response(rid, {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
                    ]
                })
            except Exception as e:
                _logger.error("Tool '%s' failed: %s", tool_name, traceback.format_exc())
                return self.send_error(rid, -32000, f"Tool error: {str(e)}")

        # ── resources/list ────────────────────────────────────────────
        if method == "resources/list":
            return self.send_response(rid, {"resources": []})

        # ── unknown ───────────────────────────────────────────────────
        _logger.warning("Unknown method: %s", method)
        return self.send_error(rid, -32601, f"Method not found: {method}")

    def run(self):
        """Main event loop: read JSON-RPC lines from stdin, process, respond."""
        self._running = True
        _logger.info("Daon Design MCP Server starting on stdio...")
        _logger.info("Registered %d tools: %s", len(TOOLS), [t["name"] for t in TOOLS])

        # Pre-warm all singletons
        try:
            _logger.info("Warming up DesignGraph...")
            dg = _get_design_graph()
            _logger.info("DesignGraph: %d components in %d categories", dg.component_count, len(dg.get_all_categories()))

            _logger.info("Warming up ComponentRetriever...")
            cr = _get_component_retriever()
            _logger.info("ComponentRetriever: index ready")

            _logger.info("Warming up StyleCardRetriever...")
            scr = _get_style_card_retriever()
            _logger.info("StyleCardRetriever: index ready")
        except Exception as e:
            _logger.error("Warm-up failed: %s", traceback.format_exc())
            # Don't crash — tools will lazy-init on first call

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                _logger.warning("Invalid JSON input: %s", line[:200])
                continue

            self.handle_request(request)

        _logger.info("Daon Design MCP Server shutting down (stdin closed).")
        self._running = False


def main():
    server = DaonDesignMCPServer()
    try:
        server.run()
    except KeyboardInterrupt:
        _logger.info("Interrupted.")
    except Exception:
        _logger.critical("Fatal: %s", traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
