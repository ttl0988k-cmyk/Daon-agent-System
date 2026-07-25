#!/usr/bin/env python3
"""
LLM Wiki Integration Tool for Hermes Agent.

Provides agent tools to query, navigate, and rescan the local LLM Wiki knowledge base.
Connects to http://127.0.0.1:19828/api/v1.
"""

import os
import json
import urllib.request
import urllib.error
import logging
from pathlib import Path
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

def _get_wiki_config() -> tuple[str, str]:
    """Retrieve LLM Wiki Base URL and Authorization Token."""
    base_url = os.getenv("LLM_WIKI_BASE_URL", "http://127.0.0.1:19828").rstrip("/")
    token = os.getenv("LLM_WIKI_TOKEN", "")
    
    if not token:
        # Fallback: check ~/.hermes/.env or profiles
        env_paths = [
            Path(__file__).resolve().parent.parent.parent / '.env',
            Path.home() / '.hermes' / '.env',
            Path.home() / '.hermes' / 'profiles' / 'raon' / '.env'
        ]
        for env_path in env_paths:
            if env_path.exists():
                try:
                    for line in env_path.read_text(encoding='utf-8', errors='ignore').splitlines():
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            if k.strip() == "LLM_WIKI_TOKEN":
                                token = v.strip().strip('"').strip("'")
                                break
                except Exception:
                    pass
            if token:
                break
                
    return base_url, token

def _call_wiki_api(method: str, path: str, payload: dict = None) -> dict:
    """Helper to communicate with LLM Wiki HTTP API."""
    base_url, token = _get_wiki_config()
    url = f"{base_url}/api/v1{path}"
    
    headers = {
        "Content-Type": "application/json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
        
    req_body = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=req_body, headers=headers, method=method)
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_content = e.read().decode("utf-8", errors="ignore")
        try:
            err_json = json.loads(err_content)
            msg = err_json.get("error", err_content)
        except Exception:
            msg = err_content or str(e)
        raise RuntimeError(f"Wiki API HTTP Error {e.code}: {msg}")
    except Exception as e:
        raise RuntimeError(f"Failed to connect to LLM Wiki API: {e}")

@registry.register(
    "llm_wiki_search",
    "Search the local LLM Wiki knowledge base using hybrid vector + keyword retrieval. Returns matches with excerpts.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query or keyword to lookup."}
        },
        "required": ["query"]
    }
)
def llm_wiki_search(query: str) -> str:
    """Performs hybrid vector+keyword search on current open project."""
    try:
        res = _call_wiki_api("POST", "/projects/current/search", {"query": query, "limit": 8})
        hits = res.get("vectorHits", []) + res.get("tokenHits", [])
        if not hits:
            return f"No results found in LLM Wiki for query: '{query}'"
            
        output = [f"Search Results for '{query}':\n"]
        seen_paths = set()
        for hit in hits:
            path = hit.get("path") or hit.get("file_path")
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            
            excerpt = hit.get("excerpt") or hit.get("text") or "No excerpt available."
            score = hit.get("vectorScore") or hit.get("score")
            score_str = f" [Score: {score:.3f}]" if score else ""
            
            output.append(f"### File: {path}{score_str}\n{excerpt.strip()}\n\n---\n")
            
        return "\n".join(output)
    except Exception as e:
        return tool_error(f"llm_wiki_search error: {e}")

@registry.register(
    "llm_wiki_read",
    "Fetch the complete content of a specific file or wiki node in the knowledge base.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The relative file path or node name to read."}
        },
        "required": ["path"]
    }
)
def llm_wiki_read(path: str) -> str:
    """Reads the content of a file or node in the active wiki project."""
    try:
        # API expects file path URL-encoded or path parameter
        import urllib.parse
        encoded_path = urllib.parse.quote(path, safe='')
        res = _call_wiki_api("GET", f"/projects/current/files/content?path={encoded_path}")
        content = res.get("content")
        if content is None:
            return f"File content not found for path: '{path}'"
        return f"### File: {path}\n\n{content}"
    except Exception as e:
        return tool_error(f"llm_wiki_read error: {e}")

@registry.register(
    "llm_wiki_graph",
    "Retrieve the wikilinks knowledge graph nodes and edges to discover connections between concepts.",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    }
)
def llm_wiki_graph() -> str:
    """Retrieves the overall wikilinks structure of the project."""
    try:
        res = _call_wiki_api("GET", "/projects/current/graph")
        nodes = res.get("nodes", [])
        edges = res.get("edges", [])
        
        output = [
            f"Knowledge Graph Summary:",
            f"- Total Nodes: {len(nodes)}",
            f"- Total Connections: {len(edges)}",
            f"\nNodes (Topics/Documents):"
        ]
        
        # List first 30 nodes for context
        for n in nodes[:30]:
            label = n.get("label") or n.get("id") or "unnamed"
            output.append(f"  * {label}")
        if len(nodes) > 30:
            output.append(f"  * ... and {len(nodes) - 30} more nodes.")
            
        return "\n".join(output)
    except Exception as e:
        return tool_error(f"llm_wiki_graph error: {e}")

@registry.register(
    "llm_wiki_rescan",
    "Trigger a background rescan of the wiki source directory to sync newly added documents.",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    }
)
def llm_wiki_rescan() -> str:
    """Forces the LLM Wiki backend to rescan source files."""
    try:
        res = _call_wiki_api("POST", "/projects/current/sources/rescan")
        status = res.get("status", "unknown")
        return f"LLM Wiki rescan triggered successfully. Status: {status}"
    except Exception as e:
        return tool_error(f"llm_wiki_rescan error: {e}")
