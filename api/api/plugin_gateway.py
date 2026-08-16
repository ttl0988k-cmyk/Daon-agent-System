"""
Plugin Gateway — DAON 외부 플러그인 import/등록/스킬 카탈로그 병합.

역할:
  1. 외부 플러그인 import: git URL 또는 로컬 폴더 → <root>/plugins/<name>/
     (Hermes `hermes plugins install` 의 git clone 모델을 DAON 서버 레벨로 재현)
  2. plugin.yaml 매니페스트 파싱: name/version/description + skills/mcp/tools/hooks
  3. 활성 플러그인의 스킬을 SkillRegistry 카탈로그에 노출
     (session 단위로 active_plugin_skills(session_id) 로 조회 → Dynamic Harness forced_skills 주입)

매니페스트 스키마 (plugin.yaml):
    name: my-plugin
    version: 1.0.0
    description: ...
    author: ...
    skills:
      - name: report-builder        # 정규화: "<plugin>:<name>" 네임스페이스
        path: skills/report-builder/SKILL.md
    mcp:                            # (선택) 기존 MCP 매니저에 연결
      - id: my-server
        command: npx
        args: [my-mcp-server]
    tools:                          # (선택) Hermes PluginManager register_tool 재사용
      - name: my_custom_tool
    hooks:                          # (선택) pre_tool_call 등
      - pre_tool_call
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from api.plugin_state import (
    get_all_plugins_state,
    get_session_plugins,
    is_plugin_globally_enabled,
    set_plugin_global_enabled,
    set_session_plugin,
)

_logger = logging.getLogger(__name__)

# 안전한 플러그인 이름 (경로 탈취 방지)
_PLUGIN_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

_manifest_cache: Dict[str, dict] = {}


# ---------------------------------------------------------------------------
# 경로 해석
# ---------------------------------------------------------------------------

def _resolve_plugins_dir() -> Path:
    """plugins/ 디렉토리: dev 는 프로젝트 루트, PyInstaller 는 _MEIPASS/plugins."""
    if hasattr(sys, '_MEIPASS'):
        base = Path(sys._MEIPASS)
        plugins_dir = base / "plugins"
        if not plugins_dir.exists():
            # exe 옆 resources/ 아래에 번들된 경우
            plugins_dir = Path(sys.executable).parent.resolve() / "plugins"
        return plugins_dir
    base = Path(__file__).resolve().parent.parent.parent  # api/api/ → api/ → root
    return base / "plugins"


def _resolve_user_plugins_dir() -> Path:
    """런타임에 설치되는 사용자 플러그인 디렉토리 (읽기/쓰기 가능).

    PyInstaller 환경에서는 번들 plugins/ 는 읽기 전용이므로,
    %LOCALAPPDATA%/Daon/plugins 로 사용자 플러그인을 저장한다.
    """
    if hasattr(sys, '_MEIPASS'):
        appdata = os.getenv('LOCALAPPDATA') or str(Path.home() / "AppData" / "Local")
        return Path(appdata) / "Daon" / "plugins"
    return _resolve_plugins_dir()


# ---------------------------------------------------------------------------
# 매니페스트 파싱
# ---------------------------------------------------------------------------

def _read_manifest(plugin_dir: Path) -> dict:
    """plugin.yaml / plugin.yml 을 읽어 dict 로 반환한다."""
    for fname in ("plugin.yaml", "plugin.yml"):
        f = plugin_dir / fname
        if f.exists():
            try:
                if yaml is None:
                    _logger.warning("PyYAML not installed — cannot parse %s", f)
                    return {}
                data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                return data if isinstance(data, dict) else {}
            except Exception as exc:
                _logger.warning("Failed to parse manifest %s: %s", f, exc)
                return {}
    return {}


def _validate_plugin_name(name: str) -> str:
    if not name or not _PLUGIN_NAME_RE.match(name):
        raise ValueError(
            f"Invalid plugin name {name!r}. Must match [a-zA-Z0-9_-]{'{1,64}'}"
        )
    return name


def normalize_plugin_manifest(manifest: dict, plugin_dir: Path) -> dict:
    """매니페스트를 DAON 표준 형태로 정규화한다 (스킬 절대경로 포함)."""
    name = _validate_plugin_name(str(manifest.get("name") or plugin_dir.name))

    skills = []
    for s in manifest.get("skills", []) or []:
        if not isinstance(s, dict):
            continue
        sname = str(s.get("name") or "").strip()
        rel_path = str(s.get("path") or "SKILL.md").strip()
        if not sname:
            continue
        skill_path = plugin_dir / rel_path
        if not skill_path.exists():
            # 패스가 없는 경우 name 을 디렉토리로 보고 SKILL.md 를 찾는다
            candidate = plugin_dir / sname / "SKILL.md"
            if candidate.exists():
                skill_path = candidate
            else:
                _logger.warning(
                    "Plugin '%s' skill '%s' path not found: %s", name, sname, skill_path
                )
                continue
        skills.append({
            "name": sname,
            "qualified": f"{name}:{sname}",
            "path": str(skill_path.resolve()),
        })

    mcp = []
    for m in manifest.get("mcp", []) or []:
        if isinstance(m, dict) and m.get("id"):
            mcp.append(m)

    tools = [str(t) for t in (manifest.get("tools", []) or []) if str(t).strip()]

    hooks = [str(h) for h in (manifest.get("hooks", []) or []) if str(h).strip()]

    return {
        "name": name,
        "version": str(manifest.get("version", "")),
        "description": str(manifest.get("description", "")),
        "author": str(manifest.get("author", "")),
        "skills": skills,
        "mcp": mcp,
        "tools": tools,
        "hooks": hooks,
        "path": str(plugin_dir.resolve()),
    }


# ---------------------------------------------------------------------------
# 플러그인 목록 / 조회
# ---------------------------------------------------------------------------

def list_installed_plugins() -> list[dict]:
    """번들 + 사용자 플러그인을 모두 스캔해 정규화된 매니페스트 목록을 반환한다."""
    result: Dict[str, dict] = {}
    state = get_all_plugins_state()
    global_enabled = state.get("global_enabled", {})

    scan_dirs = [_resolve_plugins_dir(), _resolve_user_plugins_dir()]
    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue
        for child in sorted(scan_dir.iterdir()):
            if not child.is_dir():
                continue
            manifest = _read_manifest(child)
            if not manifest:
                continue
            try:
                norm = normalize_plugin_manifest(manifest, child)
            except ValueError as exc:
                _logger.warning("Skipping invalid plugin %s: %s", child.name, exc)
                continue
            result[norm["name"]] = norm

    # 전역 ON/OFF 상태를 반영
    for name in result:
        result[name]["enabled"] = bool(global_enabled.get(name, False))

    return sorted(result.values(), key=lambda p: p["name"])


def get_plugin(plugin_name: str) -> Optional[dict]:
    _validate_plugin_name(plugin_name)
    for p in list_installed_plugins():
        if p["name"] == plugin_name:
            return p
    return None


def _sync_plugin_skill_env() -> None:
    """전역 활성 플러그인들의 루트 디렉토리를 ``DAON_PLUGIN_SKILL_DIRS`` 로 노출한다.

    Hermes 의 ``get_external_skills_dirs()`` 는 이 환경 변수를 읽어 외부 스킬
    디렉토리로 취급하므로, 전역 ON 플러그인의 SKILL.md 가 ``skills_list`` /
    ``skill_view`` / 시스템 프롬프트 스킬 인덱스에 노출된다.

    환경 변수 값이 실제로 바뀐 경우에만 Hermes 스킬 캐시(프롬프트 인덱스 +
    디스크 스냅샷)를 무효화한다.  세션 스코프 활성화는 이 메커니즘에 포함하지
    않는다 (세션별 주입은 ephemeral_system_prompt 경로로 처리).
    """
    try:
        enabled_plugins = [
            p for p in list_installed_plugins() if p.get("enabled")
        ]
        dirs = [str(Path(p["path"]).resolve()) for p in enabled_plugins if p.get("path")]
        # 정규화된(중복 제거) 목록을 원래 순서대로 유지
        unique: list[str] = []
        for d in dirs:
            if d not in unique:
                unique.append(d)
        new_value = os.pathsep.join(unique) if unique else ""
        old_value = os.environ.get("DAON_PLUGIN_SKILL_DIRS", "")
        if new_value == old_value:
            return
        if new_value:
            os.environ["DAON_PLUGIN_SKILL_DIRS"] = new_value
        else:
            os.environ.pop("DAON_PLUGIN_SKILL_DIRS", None)
        _logger.info(
            "DAON_PLUGIN_SKILL_DIRS updated: %s",
            new_value or "(none)",
        )
        _invalidate_hermes_skills_cache()
    except Exception as exc:
        _logger.warning("sync plugin skill env failed: %s", exc)


def _invalidate_hermes_skills_cache() -> None:
    """Hermes 스킬 시스템 프롬프트/디스크 스냅샷 캐시를 무효화한다."""
    try:
        from agent.prompt_builder import clear_skills_system_prompt_cache
        clear_skills_system_prompt_cache(clear_snapshot=True)
    except Exception as exc:
        _logger.debug("hermes skills cache invalidation skipped: %s", exc)


def sync_plugin_skill_env() -> None:
    """DAON(주체)에서 호출하는 진입점 — 전역 활성 플러그인을 외부 스킬로 노출."""
    try:
        _sync_plugin_skill_env()
    except Exception as exc:
        _logger.warning("sync_plugin_skill_env failed: %s", exc)


def plugin_skill_catalog_text(plugin_name: str) -> str:
    """플러그인의 스킬을 카탈로그 텍스트 형태로 반환한다 (CEO/하네스 프롬프트용)."""
    plugin = get_plugin(plugin_name)
    if not plugin or not plugin.get("skills"):
        return ""
    lines = [f"[Plugin '{plugin_name}' Skills]"]
    for s in plugin["skills"]:
        lines.append(f"- {s['qualified']}: plugin-provided skill")
    return "\n".join(lines)


def active_plugin_skills(session_id: str) -> tuple[list[str], list[str]]:
    """세션에서 활성화된 플러그인들의 (qualified 스킬 목록, 스킬 컨텐츠 블록).

    Dynamic Harness forced_skills 에 주입할 스킬 이름은 qualified
    ("plugin:skill") 로 제공하고, 실제 SKILL.md 내용은 컨텐츠 블록으로
    반환해 하네스가 참조할 수 있게 한다.
    """
    names = get_session_plugins(session_id)
    qualified: list[str] = []
    blocks: list[str] = []
    for plugin_name in names:
        plugin = get_plugin(plugin_name)
        if not plugin:
            continue
        for s in plugin.get("skills", []):
            qualified.append(s["qualified"])
            try:
                content = Path(s["path"]).read_text(encoding="utf-8")
            except Exception as exc:
                _logger.warning("Failed to read plugin skill %s: %s", s["path"], exc)
                content = ""
            blocks.append(
                f"=== PLUGIN SKILL: {s['qualified']} ===\n{content}\n=== END PLUGIN SKILL ==="
            )
    return qualified, blocks


# ---------------------------------------------------------------------------
# 외부 플러그인 import
# ---------------------------------------------------------------------------

def _is_git_url(identifier: str) -> bool:
    return (
        identifier.startswith("https://")
        or identifier.startswith("git@")
        or identifier.startswith("ssh://")
        or identifier.startswith("http://")
    )


def _repo_name_from_url(url: str) -> str:
    stem = url.rstrip("/").split("/")[-1]
    return stem.replace(".git", "") or "plugin"


def import_plugin(identifier: str, *, source_type: str = "auto", force: bool = False) -> dict:
    """외부 플러그인을 import 한다.

    identifier: git URL 또는 로컬 폴더 경로.
    source_type: "auto" | "git" | "folder"
    Returns: {name, enabled, path, description}
    """
    plugins_dir = _resolve_user_plugins_dir()
    plugins_dir.mkdir(parents=True, exist_ok=True)

    if source_type == "folder" or (source_type == "auto" and not _is_git_url(identifier)):
        # 로컬 폴더 import
        src = Path(identifier).expanduser()
        if not src.is_dir():
            raise ValueError(f"Folder not found: {identifier}")
        manifest = _read_manifest(src)
        if not manifest:
            raise ValueError(f"No plugin.yaml found in folder: {identifier}")
        name = _validate_plugin_name(str(manifest.get("name") or src.name))
        target = plugins_dir / name
        if target.exists():
            if not force:
                raise ValueError(f"Plugin '{name}' already exists (use force=True)")
            shutil.rmtree(target)
        shutil.copytree(src, target)
        imported = {"source": "folder", "identifier": identifier}

    else:
        # git clone (Hermes plugins install 모델 재현)
        git_url = identifier
        if git_url.startswith("https://") or git_url.startswith("http://"):
            _logger.info("Cloning plugin from %s", git_url)
        name = None
        with tempfile.TemporaryDirectory() as tmp:
            tmp_target = Path(tmp) / "plugin"
            try:
                result = subprocess.run(
                    ["git", "clone", "--depth", "1", git_url, str(tmp_target)],
                    capture_output=True, text=True, timeout=120,
                )
            except FileNotFoundError:
                raise ValueError("git is not installed or not in PATH") from None
            except subprocess.TimeoutExpired:
                raise ValueError("Git clone timed out after 120s") from None
            if result.returncode != 0:
                raise ValueError(f"Git clone failed: {result.stderr.strip()[:500]}")

            manifest = _read_manifest(tmp_target)
            name = _validate_plugin_name(str(manifest.get("name") or _repo_name_from_url(git_url)))
            target = plugins_dir / name
            if target.exists():
                if not force:
                    raise ValueError(f"Plugin '{name}' already exists (use force=True)")
                shutil.rmtree(target)
            shutil.move(str(tmp_target), str(target))
        imported = {"source": "git", "identifier": git_url}

    if not (target / "plugin.yaml").exists() and not (target / "__init__.py").exists():
        raise ValueError(
            f"'{name}' doesn't contain plugin.yaml or __init__.py — not a valid DAON plugin"
        )

    # 전역 ON으로 등록
    set_plugin_global_enabled(name, True)

    # 전역 활성 플러그인을 Hermes 외부 스킬 디렉토리로 노출
    sync_plugin_skill_env()

    return {
        "name": name,
        "enabled": True,
        "path": str(target.resolve()),
        "description": manifest.get("description", ""),
        "source": imported["source"],
    }


def remove_plugin(plugin_name: str) -> bool:
    """사용자 플러그인 삭제 (번들은 건드리지 않는다)."""
    _validate_plugin_name(plugin_name)
    plugins_dir = _resolve_user_plugins_dir()
    target = plugins_dir / plugin_name
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    # 상태 정리
    state = get_all_plugins_state()
    state.get("global_enabled", {}).pop(plugin_name, None)
    for sid in list(state.get("sessions", {}).keys()):
        state["sessions"][sid] = [p for p in state["sessions"].get(sid, []) if p != plugin_name]
    from api.plugin_state import _save_state
    _save_state(state)
    # 외부 스킬 노출 동기화 (삭제된 플러그인 제거)
    sync_plugin_skill_env()
    return True


def _invalidate_cache() -> None:
    _manifest_cache.clear()
    # 매니페스트 변경(import/remove) 시 외부 스킬 노출을 재동기화한다.
    sync_plugin_skill_env()
