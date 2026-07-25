"""
Skills Hub Routes — Community skill search & install API.

GET  /api/skills/search?q=...&source=...&limit=...  — Search skills from community hubs
POST /api/skills/install                            — Install a skill from a hub source
GET  /api/skills/hub/sources                         — List available search sources
"""

import sys
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_skills_hub():
    """Lazy-import the skills_hub module from hermes-agent/tools."""
    hub_path = Path(__file__).parent.parent.parent / "hermes-agent" / "tools"
    if str(hub_path) not in sys.path:
        sys.path.insert(0, str(hub_path))
    from skills_hub import SkillHub
    return SkillHub()


def handle_get_skills_hub_search(handler, parsed) -> bool:
    """GET /api/skills/search?q=...&source=...&limit=...

    Query params:
        q      — search query string (required)
        source — comma-separated source IDs: github,clawhub,skills.sh,lobehub,claude-marketplace,local
                 default: github,clawhub
        limit  — max results (default 20, max 50)

    Response:
    {
        "results": [
            {
                "name": "pdf-tools",
                "identifier": "anthropics/skills/pdf-tools",
                "description": "PDF manipulation toolkit",
                "source": "github",
                "trust_level": "trusted",
                "tags": ["pdf", "document"],
                "score": 95,
                "author": "anthropics",
                "version": "1.0.0"
            }
        ],
        "total": 5,
        "query": "pdf"
    }
    """
    import urllib.parse

    qs = parsed.query  # parsed.path has no query string — use parsed.query directly
    params = urllib.parse.parse_qs(qs)

    query = params.get("q", [""])[0].strip()
    if not query:
        handler.send_json({"error": "query parameter 'q' is required"}, 400)
        return True

    source_str = params.get("source", ["github,clawhub"])[0]
    sources = [s.strip() for s in source_str.split(",") if s.strip()]

    try:
        limit = int(params.get("limit", ["20"])[0])
        limit = max(1, min(limit, 50))
    except (ValueError, IndexError):
        limit = 20

    try:
        hub = _get_skills_hub()
        all_results = hub.search(query, limit=limit, sources=sources)

        # Convert SkillMeta objects to serializable dicts
        results = []
        for r in all_results:
            results.append({
                "name": r.name if hasattr(r, "name") else r.get("name", ""),
                "identifier": r.identifier if hasattr(r, "identifier") else r.get("identifier", ""),
                "description": r.description if hasattr(r, "description") else r.get("description", ""),
                "source": r.source if hasattr(r, "source") else r.get("source", ""),
                "trust_level": r.trust_level if hasattr(r, "trust_level") else r.get("trust_level", "community"),
                "tags": r.tags if hasattr(r, "tags") else r.get("tags", []),
                "score": getattr(r, "score", 0) if hasattr(r, "score") else r.get("score", 0),
                "author": getattr(r, "author", "") if hasattr(r, "author") else r.get("author", ""),
                "version": getattr(r, "version", "") if hasattr(r, "version") else r.get("version", ""),
            })

        handler.send_json({
            "results": results,
            "total": len(results),
            "query": query,
            "sources_searched": sources,
        })
    except ImportError as e:
        logger.warning("Skills hub import failed: %s", e)
        handler.send_json({"error": "Skills hub module not available", "results": [], "total": 0, "query": query})
    except Exception as e:
        logger.exception("Skills hub search failed")
        handler.send_json({"error": str(e), "results": [], "total": 0, "query": query})

    return True


def handle_post_skills_hub_install(handler, body: dict) -> bool:
    """POST /api/skills/install — Install a skill from a community hub.

    Body: {
        "identifier": "anthropics/skills/pdf-tools",
        "source": "github"  (optional, auto-detected)
    }

    Response: { "ok": true, "installed_to": "skills/pdf-tools", "files": [...] }
    """
    identifier = body.get("identifier", "").strip()
    if not identifier:
        handler.send_json({"ok": False, "error": "identifier is required"}, 400)
        return True

    source_hint = body.get("source", "").strip() or None

    try:
        hub = _get_skills_hub()
        bundle = hub.install(identifier, source=source_hint)

        if bundle is None:
            handler.send_json({"ok": False, "error": f"Skill not found: {identifier}"})
            return True

        # bundle can be a dict or SkillBundle object
        if hasattr(bundle, "files"):
            files_list = list(bundle.files.keys()) if bundle.files else []
            install_path = getattr(bundle, "install_path", str(Path("skills") / identifier.split("/")[-1]))
        elif isinstance(bundle, dict):
            files_list = list(bundle.get("files", {}).keys())
            install_path = bundle.get("install_path", str(Path("skills") / identifier.split("/")[-1]))
        else:
            files_list = []
            install_path = str(Path("skills") / identifier.split("/")[-1])

        handler.send_json({
            "ok": True,
            "installed_to": install_path,
            "files": files_list,
        })
    except ImportError as e:
        handler.send_json({"ok": False, "error": "Skills hub module not available"})
    except Exception as e:
        logger.exception("Skills hub install failed")
        handler.send_json({"ok": False, "error": str(e)})

    return True


def handle_get_skills_hub_sources(handler, parsed) -> bool:
    """GET /api/skills/hub/sources — List available search sources and their status.

    Response:
    {
        "sources": [
            {"id": "github", "name": "GitHub Skills", "available": true, "description": "..."},
            {"id": "clawhub", "name": "ClawHub", "available": false, "description": "..."},
            ...
        ]
    }
    """
    try:
        hub = _get_skills_hub()
        sources = []
        if hasattr(hub, "list_sources"):
            raw_sources = hub.list_sources()
            for s in raw_sources:
                if hasattr(s, "source_id"):
                    sources.append({
                        "id": s.source_id(),
                        "name": getattr(s, "__class__", type(s)).__name__,
                        "available": True,
                        "description": getattr(s, "__doc__", ""),
                    })
                elif isinstance(s, dict):
                    sources.append(s)

        if not sources:
            # Fallback: enumerate known source types
            sources = [
                {"id": "github", "name": "GitHub Skills", "available": True,
                 "description": "Search skills from GitHub repositories (openai/skills, anthropics/skills, etc.)"},
                {"id": "clawhub", "name": "ClawHub", "available": True,
                 "description": "Search the ClawHub skill registry"},
                {"id": "skills.sh", "name": "skills.sh", "available": True,
                 "description": "Search skills.sh community registry"},
                {"id": "local", "name": "Local Skills", "available": True,
                 "description": "Search locally installed skills"},
            ]

        handler.send_json({"sources": sources})
    except ImportError:
        handler.send_json({"sources": [
            {"id": "local", "name": "Local Skills", "available": True,
             "description": "Search locally installed skills"}
        ]})
    except Exception as e:
        logger.exception("Failed to list skill sources")
        handler.send_json({"error": str(e), "sources": []})

    return True


def handle_get_skills_hub_recommend(handler, parsed) -> bool:
    """GET /api/skills/recommend?workspace=... — auto-recommend community skills.

    Analyzes the workspace and searches community skill hubs for relevant skills.
    Same pattern as MCP recommend (api/mcp_recommender.py).

    Query params:
        workspace — path to the project workspace (required)
        limit     — max results per query (default 5, max 10)

    Response:
    {
        "recommendations": [{name, identifier, description, source, trust_level,
                             tags, score, label, reason, search_query, confidence}],
        "workspace_summary": {total_files, categories},
        "queries_made": ["react component", "docker container", ...],
        "total": 8
    }
    """
    from urllib.parse import parse_qs
    from pathlib import Path as _Path

    qs = parse_qs(parsed.query) if parsed.query else {}
    workspace_param = qs.get('workspace', [None])[0]

    if not workspace_param:
        handler.send_json({"error": "Missing ?workspace= parameter", "recommendations": []}, 400)
        return True

    ws_path = _Path(workspace_param)
    if not ws_path.exists():
        handler.send_json({"error": f"Workspace not found: {workspace_param}", "recommendations": []})
        return True

    limit = 5
    try:
        limit = int(qs.get('limit', ['5'])[0])
        limit = max(1, min(limit, 10))
    except (ValueError, IndexError):
        pass

    try:
        from api.skills_recommender import recommend_skills
        result = recommend_skills(str(ws_path), max_per_query=limit)
        handler.send_json(result)
    except ImportError as e:
        logger.warning("Skills recommender import failed: %s", e)
        handler.send_json({"error": "Skills recommender not available", "recommendations": []})
    except Exception as e:
        logger.exception("Skills recommend failed")
        handler.send_json({"error": str(e), "recommendations": []})

    return True
