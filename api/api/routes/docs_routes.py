"""
Daon Agent System — Auto Documentation routes.

Provides automated codebase analysis and document generation:
  POST /api/docs/generate — run document generation for a session workspace
  GET  /api/docs/status  — check generation progress / list generated docs
"""
import json
import logging
import threading
import time
import os
import subprocess
import traceback
from pathlib import Path
from urllib.parse import parse_qs
from datetime import datetime

from api.helpers import require, bad, j
from api.models import get_session

_logger = logging.getLogger(__name__)

# ── In-memory job tracking ──
_docs_jobs = {}          # job_id -> {status, progress, result, ...}
_docs_jobs_lock = threading.Lock()

_DEFAULT_OUTPUT_DIR = "docs"

_DOC_TYPES = [
    {"id": "readme", "label": "README.md", "desc": "프로젝트 개요, 설치 방법, 사용법"},
    {"id": "architecture", "label": "ARCHITECTURE.md", "desc": "시스템 아키텍처, 데이터 흐름"},
    {"id": "api", "label": "API 문서", "desc": "엔드포인트 명세, 요청/응답 예시"},
    {"id": "modules", "label": "모듈 문서", "desc": "각 모듈/클래스/함수 설명"},
    {"id": "config", "label": "설정 가이드", "desc": "config.yaml, .env 설정 설명"},
]


# ── Helper: discover project structure ──

def _discover_project_structure(workspace: Path) -> dict:
    """Walk the workspace and categorize files by type."""
    categories = {
        "python": [], "javascript": [], "config": [], "markdown": [],
        "html": [], "css": [], "data": [], "other": [],
    }
    ext_map = {
        ".py": "python", ".js": "javascript", ".ts": "javascript",
        ".jsx": "javascript", ".tsx": "javascript",
        ".yaml": "config", ".yml": "config", ".toml": "config",
        ".json": "config", ".env": "config", ".cfg": "config",
        ".md": "markdown", ".mdx": "markdown",
        ".html": "html", ".htm": "html",
        ".css": "css", ".scss": "css", ".less": "css",
        ".csv": "data", ".sqlite": "data", ".db": "data",
        ".txt": "data", ".log": "data",
    }
    skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv',
                 'build', 'dist', 'dist_new', '.idea', '.vscode', 'data'}

    total_files = 0
    for root, dirs, files in os.walk(str(workspace)):
        # Skip hidden/special directories
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.')]

        rel_root = Path(root).relative_to(workspace)
        for fname in files:
            total_files += 1
            ext = Path(fname).suffix.lower()
            cat = ext_map.get(ext, "other")
            rel_path = str(rel_root / fname) if str(rel_root) != '.' else fname
            categories[cat].append(rel_path)

    return {
        "total_files": total_files,
        "categories": {k: v for k, v in categories.items() if v},
        "workspace_root": str(workspace),
    }


def _read_file_safe(filepath: Path, max_lines: int = 200) -> str | None:
    """Safely read a file, returning first max_lines lines."""
    try:
        content = filepath.read_text(encoding='utf-8', errors='replace')
        lines = content.splitlines()
        if len(lines) > max_lines:
            return '\n'.join(lines[:max_lines]) + f'\n... ({len(lines) - max_lines} more lines)'
        return content
    except Exception:
        return None


def _collect_key_files(workspace: Path, structure: dict) -> list[dict]:
    """Collect key files for documentation analysis."""
    key_files = []

    # Always include config files
    for cfg_file in ['config.yaml', 'config.yml', '.env', 'package.json', 'setup.py', 'pyproject.toml']:
        p = workspace / cfg_file
        if p.exists():
            content = _read_file_safe(p, 100)
            key_files.append({"path": cfg_file, "type": "config", "content": content})

    # Entry point files
    for entry in ['server.py', 'main.py', 'app.py', 'index.js', 'index.ts']:
        p = workspace / entry
        if p.exists():
            content = _read_file_safe(p, 300)
            key_files.append({"path": entry, "type": "entry", "content": content})

    # Route files (for API docs)
    for cat in ['python']:
        for rel_path in structure.get("categories", {}).get(cat, []):
            if 'route' in rel_path.lower() or 'router' in rel_path.lower():
                abs_path = workspace / rel_path
                content = _read_file_safe(abs_path, 200)
                if content:
                    key_files.append({"path": rel_path, "type": "routes", "content": content})

    # Core module files (api/, src/, lib/)
    for cat in ['python', 'javascript']:
        for rel_path in structure.get("categories", {}).get(cat, []):
            parts = Path(rel_path).parts
            if parts and parts[0] in ('api', 'src', 'lib', 'core', 'modules'):
                if 'route' not in rel_path.lower():
                    abs_path = workspace / rel_path
                    content = _read_file_safe(abs_path, 150)
                    if content:
                        key_files.append({"path": rel_path, "type": "module", "content": content})

    # Limit to avoid overwhelming context
    return key_files[:50]


def _generate_document(doc_type: str, workspace: Path, structure: dict, key_files: list[dict]) -> str:
    """Generate a single document based on collected project data."""
    ws_name = workspace.name
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if doc_type == "readme":
        return _generate_readme(workspace, structure, key_files, ws_name, now)
    elif doc_type == "architecture":
        return _generate_architecture(workspace, structure, key_files, ws_name, now)
    elif doc_type == "api":
        return _generate_api_docs(workspace, key_files, ws_name, now)
    elif doc_type == "modules":
        return _generate_module_docs(workspace, key_files, ws_name, now)
    elif doc_type == "config":
        return _generate_config_guide(workspace, key_files, ws_name, now)
    else:
        return f"# {doc_type.upper()}\n\nUnknown document type.\n"


def _generate_readme(workspace: Path, structure: dict, key_files: list[dict], name: str, now: str) -> str:
    """Generate README.md from project analysis."""
    cats = structure.get("categories", {})
    py_count = len(cats.get("python", []))
    js_count = len(cats.get("javascript", []))
    cfg_count = len(cats.get("config", []))
    html_count = len(cats.get("html", []))
    css_count = len(cats.get("css", []))

    # Detect tech stack
    tech_stack = []
    if py_count > 0:
        tech_stack.append("Python")
    if js_count > 0:
        tech_stack.append("JavaScript/Node.js")
    if html_count > 0:
        tech_stack.append("HTML")
    if css_count > 0:
        tech_stack.append("CSS")

    # Detect framework hints from key files
    frameworks = []
    for f in key_files:
        content = f.get("content", "") or ""
        if "fastapi" in content.lower():
            frameworks.append("FastAPI")
        if "flask" in content.lower():
            frameworks.append("Flask")
        if "react" in content.lower():
            frameworks.append("React")
        if "vue" in content.lower():
            frameworks.append("Vue.js")
        if "express" in content.lower():
            frameworks.append("Express")
        if "playwright" in content.lower():
            frameworks.append("Playwright")
        if "pydantic" in content.lower():
            frameworks.append("Pydantic")
        if "aiohttp" in content.lower():
            frameworks.append("aiohttp")

    tech_line = ", ".join(tech_stack) if tech_stack else "여러 언어"
    fw_line = ", ".join(dict.fromkeys(frameworks[:5])) if frameworks else "커스텀"

    # Build directory tree
    tree_lines = []
    for category, files in sorted(cats.items()):
        if files:
            tree_lines.append(f"├── {category}/ ({len(files)} files)")

    tree_str = "\n".join(tree_lines) if tree_lines else "├── (no categorized files)"

    # Detect entry point
    entry_point = "server.py" if (workspace / "server.py").exists() else "main.py"

    readme = f"""# {name}

> 프로젝트 개요 — 자동 생성됨: {now}

## 📋 주요 기능

- 코드베이스 분석 및 문서 자동 생성
- 멀티 에이전트 협업 시스템
- 웹 기반 대시보드 및 실시간 모니터링
- 파일 탐색 및 코드 편집기 내장
- Git 자동화 (커밋, 푸시, 풀)
- 음성 입력 지원 (Whisper)
- 브라우저 자동화 (Playwright)

## 🛠 기술 스택

- **언어**: {tech_line}
- **프레임워크/라이브러리**: {fw_line}
- **파일 구성**: 총 {structure.get("total_files", 0)}개 파일

## 🚀 빠른 시작

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 설정
# config.yaml 파일을 편집하여 API 키와 설정을 입력하세요.

# 3. 실행
python {entry_point}
```

## 📁 프로젝트 구조

```
{name}/
{tree_str}
```

## ⚙️ 환경 변수

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `PORT` | 서버 포트 | `9090` |
| `HERMES_HOME` | Hermes 설정 디렉토리 | `~/.hermes` |
| `OPENAI_API_KEY` | OpenAI API 키 | - |

## 📄 라이선스

이 프로젝트는 내부 사용 목적으로 제작되었습니다.
"""
    return readme


def _generate_architecture(workspace: Path, structure: dict, key_files: list[dict], name: str, now: str) -> str:
    """Generate ARCHITECTURE.md from project analysis."""
    cats = structure.get("categories", {})

    # Build module dependency summary from key files
    module_deps = {}
    imports_found = set()
    for f in key_files:
        content = f.get("content", "") or ""
        fname = f["path"]
        fmod = Path(fname).stem
        deps = []
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("from ") or line.startswith("import "):
                # Extract module name
                parts = line.split()
                if len(parts) >= 2:
                    mod = parts[1].split(".")[0]
                    if mod not in ("os", "sys", "json", "time", "datetime", "pathlib",
                                   "typing", "logging", "threading", "re", "collections"):
                        deps.append(mod)
                        imports_found.add(mod)
        if deps:
            module_deps[fname] = list(dict.fromkeys(deps))  # unique

    # Generate Mermaid diagram
    mermaid = "```mermaid\ngraph TD\n"
    mermaid += f"    A[Web UI<br/>index.html] --> B[{'server.py' if (workspace / 'server.py').exists() else 'Server'}]\n"
    mermaid += "    B --> C[API Routes]\n"
    mermaid += "    B --> D[Streaming Engine]\n"
    mermaid += "    C --> E[Session Manager]\n"
    mermaid += "    C --> F[File Operations]\n"
    mermaid += "    C --> G[Browser Automation]\n"
    mermaid += "    C --> H[Git Automation]\n"
    mermaid += "    D --> I[AIAgent]\n"
    mermaid += "    I --> J[LLM Provider]\n"
    mermaid += "```"

    # Data flow description
    data_flow = """1. **사용자 입력** → `index.html`에서 WebSocket/SSE로 전송
2. **라우팅** → `api/routes/__init__.py`에서 경로 분기
3. **세션 관리** → `api/models.py`에서 세션 상태 관리
4. **에이전트 실행** → `api/streaming.py`에서 AIAgent 인스턴스 생성 및 스트리밍
5. **LLM 호출** → 설정된 Provider를 통해 API 호출
6. **결과 스트리밍** → SSE로 클라이언트에 토큰 단위 전송"""

    arch = f"""# {name} — 시스템 아키텍처

> 자동 생성됨: {now}

## 🏗 시스템 개요

```
┌─────────────┐     SSE/HTTP      ┌──────────────┐     API Call     ┌──────────────┐
│   Web UI    │ ◄──────────────► │  Python Server │ ◄─────────────► │  LLM Provider │
│ (index.html)│                   │  (server.py)   │                 │ (OpenAI/etc)  │
└─────────────┘                   └──────┬─────────┘                 └──────────────┘
                                         │
                              ┌──────────┼──────────┐
                              │          │          │
                         ┌────▼───┐ ┌───▼────┐ ┌───▼────┐
                         │Session │ │ Agent  │ │  File  │
                         │Manager │ │Runner  │ │  Ops   │
                         └────────┘ └────────┘ └────────┘
```

## 📊 데이터 흐름

{data_flow}

## 🔗 모듈 의존성

{mermaid}

## 📦 모듈 설명

| 모듈 | 설명 | 의존성 |
|------|------|--------|
| `server.py` | 메인 서버 진입점 | 모든 라우트 모듈 |
| `api/config.py` | 설정 관리, 환경변수 로드 | profiles |
| `api/models.py` | 세션 데이터 모델 | - |
| `api/streaming.py` | SSE 스트리밍 엔진 | AIAgent |
| `api/routes/` | API 엔드포인트 핸들러 | models, streaming |
| `api/managers/` | 모델 관리자 | auth.json |

## 🎯 디자인 결정 (ADR)

1. **SSE 기반 스트리밍**: WebSocket 대신 Server-Sent Events를 선택하여 단방향 토큰 스트리밍에 최적화
2. **파일 기반 세션**: JSON 파일로 세션 저장 → 백업/마이그레이션 용이
3. **Thread-local 환경변수**: 동시 세션 처리를 위해 스레드별 환경변수 컨텍스트 사용
4. **스킬 시스템**: YAML frontmatter + Markdown으로 에이전트 행동 가이드라인 주입
"""
    return arch


def _generate_api_docs(workspace: Path, key_files: list[dict], name: str, now: str) -> str:
    """Generate API documentation from route analysis."""
    api_sections = []

    # Scan route files for endpoint patterns
    for f in key_files:
        if f.get("type") not in ("routes", "entry"):
            continue
        content = f.get("content", "") or ""
        fname = f["path"]

        # Extract route definitions
        endpoints = []
        for line in content.splitlines():
            line_stripped = line.strip()
            # Match common route patterns
            if "parsed.path ==" in line_stripped or "parsed.path ==" in line_stripped:
                parts = line_stripped.split("'")
                if len(parts) >= 2:
                    path = parts[1]
                    endpoints.append({"path": path, "source": fname})

        if endpoints:
            api_sections.append(f"### {Path(fname).stem}\n")
            for ep in endpoints[:20]:  # Limit per file
                method = "GET" if "handle_get" in fname.lower() or "get_" in ep["path"].lower() else "POST"
                api_sections.append(f"- **{method}** `{ep['path']}`")
            api_sections.append("")

    ep_list = "\n".join(api_sections) if api_sections else "_라우트 패턴을 자동 감지하지 못했습니다. 수동 확인이 필요합니다._"

    api_doc = f"""# {name} — API 문서

> 자동 생성됨: {now}

## 🔐 인증

현재 이 프로젝트는 로컬 전용으로 설계되어 있으며, 선택적으로 비밀번호 인증을 사용할 수 있습니다.

## 📡 엔드포인트 목록

{ep_list}

## 📥 요청 형식

- **Content-Type**: `application/json`
- **인코딩**: UTF-8

## 📤 응답 형식

```json
{{
  "success": true,
  "data": {{}},
  "error": null
}}
```

## ⚠️ 에러 코드

| 코드 | 설명 |
|------|------|
| 400 | 잘못된 요청 (필수 필드 누락 등) |
| 401 | 인증 필요 |
| 404 | 리소스를 찾을 수 없음 |
| 500 | 서버 내부 오류 |
"""
    return api_doc


def _generate_module_docs(workspace: Path, key_files: list[dict], name: str, now: str) -> str:
    """Generate module-level documentation."""
    sections = []

    for f in key_files:
        if f.get("type") not in ("module", "entry"):
            continue
        fpath = f["path"]
        content = f.get("content", "") or ""

        # Extract class/function definitions
        classes = []
        functions = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("class ") and ":" in stripped:
                cls_name = stripped.split("class ")[1].split("(")[0].split(":")[0].strip()
                classes.append(cls_name)
            elif stripped.startswith("def ") and ":" in stripped:
                func_name = stripped.split("def ")[1].split("(")[0].strip()
                if not func_name.startswith("_"):
                    functions.append(func_name)

        if classes or functions:
            sections.append(f"### `{fpath}`\n")
            if classes:
                sections.append("**클래스**: " + ", ".join(f"`{c}`" for c in classes[:10]))
            if functions:
                sections.append("**주요 함수**: " + ", ".join(f"`{fn}`" for fn in functions[:15]))
            sections.append("")

    combined = "\n".join(sections) if sections else "_모듈 정보를 자동 추출하지 못했습니다._"

    mod_doc = f"""# {name} — 모듈 문서

> 자동 생성됨: {now}

## 📦 모듈 목록

{combined}

## 📝 모듈 작성 가이드

- 모든 public 함수/클래스에는 docstring을 작성하세요.
- 타입 힌트를 적극 활용하세요.
- 모듈당 단일 책임 원칙을 지켜주세요.
"""
    return mod_doc


def _generate_config_guide(workspace: Path, key_files: list[dict], name: str, now: str) -> str:
    """Generate configuration guide."""
    config_entries = []

    for f in key_files:
        if f.get("type") != "config":
            continue
        fpath = f["path"]
        content = f.get("content", "") or ""

        # Extract keys from YAML/JSON/env
        keys = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("//"):
                continue
            if ":" in stripped and not stripped.startswith(" ") and not stripped.startswith("-"):
                key = stripped.split(":")[0].strip().strip('"').strip("'")
                if key and not key.startswith("#"):
                    keys.append(key)

        if keys:
            config_entries.append(f"### `{fpath}`\n")
            for k in keys[:15]:
                config_entries.append(f"- `{k}`")
            config_entries.append("")

    combined = "\n".join(config_entries) if config_entries else "_설정 파일을 찾지 못했습니다._"

    guide = f"""# {name} — 설정 가이드

> 자동 생성됨: {now}

## ⚙️ 설정 파일 목록

{combined}

## 🔑 환경 변수 (.env)

| 변수명 | 설명 | 예시 |
|--------|------|------|
| `PORT` | 서버 포트 번호 | `9090` |
| `OPENAI_API_KEY` | OpenAI API 키 | `sk-...` |
| `HERMES_HOME` | Hermes 홈 디렉토리 | `~/.hermes` |
| `MINIMAX_API_KEY` | MiniMax API 키 | - |
| `DEEPSEEK_API_KEY` | DeepSeek API 키 | - |

## 📝 설정 변경 방법

1. `config.yaml` 파일을 직접 편집하거나
2. Web UI의 Settings 메뉴에서 변경할 수 있습니다.
3. 변경사항은 서버 재시작 없이 적용됩니다.
"""
    return guide


# ── Background document generation worker ──

def _run_doc_generation(job_id: str, workspace: Path, doc_types: list[str], output_dir: str):
    """Run document generation in background thread."""
    try:
        with _docs_jobs_lock:
            _docs_jobs[job_id]["status"] = "running"
            _docs_jobs[job_id]["progress"] = 5

        # Step 1: Discover structure
        _update_progress(job_id, 10, "프로젝트 구조 분석 중...")
        structure = _discover_project_structure(workspace)

        # Step 2: Collect key files
        _update_progress(job_id, 25, "핵심 파일 수집 중...")
        key_files = _collect_key_files(workspace, structure)

        # Step 3: Generate documents (with timestamp to avoid overwriting)
        total = len(doc_types)
        generated = []
        warnings = []
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        for idx, doc_type in enumerate(doc_types):
            progress = 30 + int((idx / max(total, 1)) * 60)
            _update_progress(job_id, progress, f"문서 생성 중: {doc_type}...")

            try:
                content = _generate_document(doc_type, workspace, structure, key_files)
                doc_name = f"{doc_type.upper() if doc_type == 'api' else doc_type.title()}_{ts}.md"
                out_path = workspace / output_dir / doc_name
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(content, encoding='utf-8')

                generated.append({
                    "path": str(out_path.relative_to(workspace)),
                    "type": doc_type,
                    "size_bytes": len(content.encode('utf-8')),
                })
            except Exception as gen_err:
                warnings.append(f"{doc_type} 생성 실패: {gen_err}")

        # Check if output_dir is docs/ (default) — also generate a summary
        if doc_types and "readme" in doc_types:
            # README goes to workspace root
            pass  # already written to docs/ or user-specified dir

        _update_progress(job_id, 95, "마무리 중...")

        with _docs_jobs_lock:
            _docs_jobs[job_id].update({
                "status": "completed",
                "progress": 100,
                "message": f"{len(generated)}개 문서 생성 완료",
                "result": {
                    "files_analyzed": structure.get("total_files", 0),
                    "docs_generated": generated,
                    "output_dir": output_dir,
                    "warnings": warnings,
                }
            })

    except Exception as e:
        _logger.error("Document generation failed: %s", traceback.format_exc())
        with _docs_jobs_lock:
            _docs_jobs[job_id].update({
                "status": "error",
                "progress": 0,
                "message": str(e),
            })


def _update_progress(job_id: str, progress: int, message: str):
    """Update job progress in a thread-safe manner."""
    with _docs_jobs_lock:
        if job_id in _docs_jobs:
            _docs_jobs[job_id]["progress"] = progress
            _docs_jobs[job_id]["message"] = message


# ── Route Handlers ──

def handle_post_docs_generate(handler, body: dict) -> bool:
    """POST /api/docs/generate — start document generation job."""
    sid = body.get('session_id', '')
    doc_types = body.get('doc_types', [])
    output_dir = body.get('output_dir', _DEFAULT_OUTPUT_DIR)

    # Validate session
    if not sid:
        return bad(handler, 'session_id is required')

    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, 'Session not found')

    workspace = Path(s.workspace)
    if not workspace.exists():
        return bad(handler, f'Workspace not found: {workspace}')

    # Validate doc_types
    if not doc_types:
        doc_types = [d["id"] for d in _DOC_TYPES]  # all types by default

    valid_types = {d["id"] for d in _DOC_TYPES}
    invalid = [t for t in doc_types if t not in valid_types]
    if invalid:
        return bad(handler, f'Invalid doc types: {", ".join(invalid)}. Valid: {", ".join(valid_types)}')

    # Validate output_dir (no path traversal)
    output_dir = output_dir.replace('\\', '/').strip('/')
    if '..' in output_dir:
        return bad(handler, 'Invalid output_dir')

    # Create job
    job_id = f"docs_{int(time.time() * 1000)}"
    with _docs_jobs_lock:
        _docs_jobs[job_id] = {
            "status": "pending",
            "progress": 0,
            "message": "시작 대기 중...",
            "created_at": time.time(),
            "workspace": str(workspace),
            "doc_types": doc_types,
            "output_dir": output_dir,
        }

    # Start background thread
    thread = threading.Thread(
        target=_run_doc_generation,
        args=(job_id, workspace, doc_types, output_dir),
        daemon=True,
    )
    thread.start()

    return j(handler, {
        "success": True,
        "job_id": job_id,
        "message": "문서 생성이 시작되었습니다.",
        "doc_types": doc_types,
        "output_dir": output_dir,
    })


def handle_get_docs_status(handler, parsed) -> bool:
    """GET /api/docs/status?job_id=... — check document generation progress."""
    qs = parse_qs(parsed.query)
    job_id = (qs.get('job_id', [''])[0]) if isinstance(qs.get('job_id'), list) else qs.get('job_id', '')

    if job_id:
        with _docs_jobs_lock:
            job = _docs_jobs.get(job_id)
        if not job:
            return bad(handler, f'Job not found: {job_id}')
        return j(handler, {"job_id": job_id, **job})

    # No job_id — return recent jobs summary
    with _docs_jobs_lock:
        recent = []
        for jid, jdata in list(_docs_jobs.items())[-20:]:
            recent.append({
                "job_id": jid,
                "status": jdata.get("status"),
                "message": jdata.get("message"),
                "created_at": jdata.get("created_at"),
            })

    return j(handler, {
        "recent_jobs": list(reversed(recent)),
        "available_doc_types": _DOC_TYPES,
    })


def handle_get_docs_list(handler, parsed) -> bool:
    """GET /api/docs/list?session_id=... — list generated docs in workspace."""
    qs = parse_qs(parsed.query)
    sid = (qs.get('session_id', [''])[0]) if isinstance(qs.get('session_id'), list) else qs.get('session_id', '')

    if not sid:
        return bad(handler, 'session_id is required')

    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, 'Session not found')

    workspace = Path(s.workspace)
    docs_dirs = ['docs', 'documentation', 'wiki']

    found_docs = []
    for ddir in docs_dirs:
        dpath = workspace / ddir
        if dpath.exists() and dpath.is_dir():
            for f in sorted(dpath.rglob("*.md")):
                try:
                    rel = str(f.relative_to(workspace))
                    size = f.stat().st_size
                    found_docs.append({
                        "path": rel,
                        "name": f.name,
                        "size_bytes": size,
                        "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                    })
                except Exception:
                    pass

    # Also check root README.md
    root_readme = workspace / "README.md"
    if root_readme.exists():
        found_docs.insert(0, {
            "path": "README.md",
            "name": "README.md",
            "size_bytes": root_readme.stat().st_size,
            "modified": datetime.fromtimestamp(root_readme.stat().st_mtime).isoformat(),
        })

    return j(handler, {
        "docs": found_docs,
        "workspace": str(workspace),
    })
