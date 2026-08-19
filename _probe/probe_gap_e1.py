"""Gap E-1 feature probe: target binding + daon-self-knowledge skill.

Verifies without starting the server:
  1. The daon-self-knowledge skill files exist and parse.
  2. SkillRegistry exposes the skill in the catalog (curated/approved).
  3. Workspace presets inject the DAON Repo entry idempotently.

Run: python _probe/probe_gap_e1.py  (from the workspace root)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

_checks = 0


def check(cond, msg):
    global _checks
    assert cond, msg
    _checks += 1
    print(f"  [OK] {msg}")


# ---------------------------------------------------------------------------
# Group 1: skill files on disk
# ---------------------------------------------------------------------------
print("Group 1: skill files")
skill_dir = ROOT / "skills" / "System" / "daon-self-knowledge"
md_file = skill_dir / "SKILL.md"
yaml_file = skill_dir / "skill.yaml"
check(md_file.is_file(), "SKILL.md exists")
check(yaml_file.is_file(), "skill.yaml exists")

import yaml  # noqa: E402

meta = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
check(isinstance(meta, dict) and meta.get("name") == "daon-self-knowledge",
      "skill.yaml parses and name matches")
check(meta.get("category") == "System", "skill.yaml category is System")
check(meta.get("priority") == "high", "skill.yaml priority is high")

md_text = md_file.read_text(encoding="utf-8")
for kw in ("_sync_build.py", "daon-server.spec", "electron-builder",
           ".cmd", "_probe"):
    check(kw in md_text, f"SKILL.md mentions {kw}")

# ---------------------------------------------------------------------------
# Group 2: skill catalog exposure (the E-1 acceptance criterion)
# ---------------------------------------------------------------------------
print("Group 2: skill catalog exposure")
from api.skill_registry import SkillRegistry, SKILL_APPROVED  # noqa: E402

registry = SkillRegistry()
entry = registry.get_skill("daon-self-knowledge")
check(entry is not None, "get_skill('daon-self-knowledge') found")
check(entry.source == "curated", "source is curated")
check(entry.lifecycle == SKILL_APPROVED, "lifecycle is approved")
check(entry.category == "System", "category is System")
check(entry.priority == "high", "priority is high")
check(entry.version == "1.0", "version is 1.0")
check(entry.label != "", "label is set")
check("self-knowledge" in entry.capabilities, "capabilities include self-knowledge")
check("build-pipeline" in entry.capabilities, "capabilities include build-pipeline")
check(any(t in entry.trigger for t in ("daon", "build", "probe")),
      "trigger includes daon/build/probe")
check("ouroboros" in entry.tags, "tags include ouroboros")
check("gap-e" in entry.tags, "tags include gap-e")

catalog = registry.get_catalog_text()
check("daon-self-knowledge" in catalog, "catalog text exposes the skill")
check("[Curated Skills - Always Available]" in catalog,
      "skill listed under curated section")

injected = registry.load_skills(["daon-self-knowledge"])
check("SKILL: daon-self-knowledge" in injected, "load_skills injects the skill")
check("_sync_build.py" in injected, "injected content carries build knowledge")

# ---------------------------------------------------------------------------
# Group 3: workspace presets (DAON target binding)
# ---------------------------------------------------------------------------
print("Group 3: workspace presets")
import api.workspace as ws  # noqa: E402

root = ws._resolve_daon_repo_root()
check(root is not None and root.resolve() == ROOT,
      "_resolve_daon_repo_root returns the project root in dev")

presets = ws.get_workspace_presets()
check(len(presets) == 1, "exactly one preset exposed")
check(presets[0]["name"] == "DAON Repo", "preset name is DAON Repo")
check(Path(presets[0]["path"]).resolve() == ROOT, "preset path is the repo root")
check((ROOT / "server.py").exists(), "server.py marker present at repo root")

# Empty list -> preset injected
out = ws.ensure_workspace_presets([])
check(any(w.get("name") == "DAON Repo" for w in out),
      "empty list gets the preset injected")
check(len(out) == 1, "no extra entries injected")

# Existing entry with same path (different case) -> no duplicate
orig = [{"path": str(ROOT).upper(), "name": "My Copy"}]
out = ws.ensure_workspace_presets(orig)
check(len(out) == 1, "same path (case-insensitive) not duplicated")
check(orig == [{"path": str(ROOT).upper(), "name": "My Copy"}],
      "original list not mutated")

# Idempotence
once = ws.ensure_workspace_presets([{"path": "C:/fake/proj", "name": "Fake"}])
twice = ws.ensure_workspace_presets(once)
check(once == twice, "ensure_workspace_presets is idempotent")
check(sum(1 for w in once if w.get("name") == "DAON Repo") == 1,
      "exactly one DAON Repo entry after injection")

# load_workspaces wraps the base loader with preset injection
saved_base = ws._load_workspaces_base
try:
    ws._load_workspaces_base = lambda: [{"path": "C:/fake/proj", "name": "Fake"}]
    loaded = ws.load_workspaces()
    check(any(w.get("name") == "Fake" for w in loaded),
          "load_workspaces keeps base entries")
    check(any(w.get("name") == "DAON Repo" for w in loaded),
          "load_workspaces injects the preset")
finally:
    ws._load_workspaces_base = saved_base

# Real load_workspaces (read-only path) always exposes the repo path,
# either as a pre-existing entry (possibly under another name) or as the
# injected preset.
real = ws.load_workspaces()
real_paths = set()
for w in real:
    try:
        real_paths.add(str(Path(w.get("path", "")).resolve()).lower())
    except Exception:
        pass
check(str(ROOT.resolve()).lower() in real_paths,
      "real load_workspaces exposes the DAON repo path")

print(f"\nALL GAP-E1 PROBES PASSED ({_checks} checks)")
