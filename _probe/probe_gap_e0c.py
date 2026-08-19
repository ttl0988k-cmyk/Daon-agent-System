#!/usr/bin/env python3
"""Gap E-0c probe: plugin_create tool_template option.

Verifies (without a running DAON server):
  1. tool_template scaffolds __init__.py with register(ctx) + ctx.register_tool
     and declares the tool in the plugin.yaml 'tools' list (tool-only plugin:
     no SKILL.md when skill_name is omitted).
  2. tool_template + explicit skill_name scaffolds both tool and skill.
  3. Without tool_template the legacy scaffold is unchanged (no __init__.py).
  4. The generated __init__.py actually imports and register(ctx) calls
     ctx.register_tool with the expected name/toolset/schema/handler/check_fn
     (mirrors PluginManager._load_directory_module import mechanics).
  5. Input validation rejects bad tool_template names.
  6. PLUGIN_CREATE_SCHEMA exposes tool_template/tool_description and the
     registry handler forwards them.

Run from the repo root:  python _probe/probe_gap_e0c.py
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hermes-agent"))
sys.path.insert(0, str(ROOT / "api"))

# ---------------------------------------------------------------------------
# Fake gateway: capture the scaffolded plugin dir before the tempdir vanishes.
# ---------------------------------------------------------------------------

_captured: list[dict] = []


def _fake_import_plugin(path: str, source_type: str = "auto", force: bool = False) -> dict:
    src = Path(path)
    if not (src / "plugin.yaml").exists():
        raise RuntimeError("scaffold missing plugin.yaml")
    dest_root = Path(tempfile.mkdtemp(prefix="e0c_capture_"))
    dest = dest_root / src.name
    shutil.copytree(src, dest)
    _captured.append({"dir": dest})
    return {"ok": True, "imported": src.name, "source_type": source_type}


_fake_pg = types.SimpleNamespace(import_plugin=_fake_import_plugin)

import tools.plugin_manager_tool as pmt  # noqa: E402

pmt._load_gateway = lambda: _fake_pg  # type: ignore[method-assign]

_failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    suffix = f"  ({detail})" if detail and not cond else ""
    print(f"[{status}] {label}{suffix}")
    if not cond:
        _failures.append(label)


# ---------------------------------------------------------------------------
# 1. tool-only scaffold (tool_template set, no skill_name)
# ---------------------------------------------------------------------------

res1 = pmt._plugin_create(
    name="probe-e0c-toolonly",
    description="probe tool-only plugin",
    tool_template="probe_lookup",
    tool_description="Probe lookup tool",
)
check("1a tool-only create ok", res1.get("ok") is True and res1.get("scaffolded") is True,
      json.dumps(res1, ensure_ascii=False)[:200])
check("1b result reports tool", res1.get("tool", {}).get("name") == "probe_lookup"
      and res1.get("tool", {}).get("toolset") == "plugin-probe-e0c-toolonly")
check("1c tool-only has no skill key", "skill" not in res1)

cap1 = _captured[-1]["dir"]
yaml1 = (cap1 / "plugin.yaml").read_text(encoding="utf-8")
check("1d yaml declares tools list", "tools:\n  - probe_lookup\n" in yaml1, yaml1[:300])
check("1e yaml has no skills block", "skills:" not in yaml1)
check("1f __init__.py generated", (cap1 / "__init__.py").exists())
check("1g no SKILL.md in tool-only mode", not (cap1 / "skills").exists())

# ---------------------------------------------------------------------------
# 2. tool + skill scaffold (explicit skill_name alongside tool_template)
# ---------------------------------------------------------------------------

res2 = pmt._plugin_create(
    name="probe-e0c-both",
    tool_template="probe_calc",
    skill_name="probe-skill",
)
check("2a both-mode create ok", res2.get("ok") is True)
check("2b result reports both", res2.get("tool", {}).get("name") == "probe_calc"
      and res2.get("skill", {}).get("name") == "probe-skill")

cap2 = _captured[-1]["dir"]
yaml2 = (cap2 / "plugin.yaml").read_text(encoding="utf-8")
check("2c yaml declares both blocks",
      "tools:\n  - probe_calc\n" in yaml2 and "- name: probe-skill" in yaml2)
check("2d SKILL.md present", (cap2 / "skills" / "probe-skill" / "SKILL.md").exists())
check("2e __init__.py present", (cap2 / "__init__.py").exists())

# ---------------------------------------------------------------------------
# 3. legacy scaffold unchanged (no tool_template)
# ---------------------------------------------------------------------------

res3 = pmt._plugin_create(name="probe-e0c-legacy")
check("3a legacy create ok", res3.get("ok") is True)
check("3b legacy has skill, no tool", res3.get("skill", {}).get("name") == "probe-e0c-legacy"
      and "tool" not in res3)

cap3 = _captured[-1]["dir"]
yaml3 = (cap3 / "plugin.yaml").read_text(encoding="utf-8")
check("3c legacy yaml has no tools block", "tools:" not in yaml3)
check("3d legacy has no __init__.py", not (cap3 / "__init__.py").exists())
check("3e legacy SKILL.md present",
      (cap3 / "skills" / "probe-e0c-legacy" / "SKILL.md").exists())

# ---------------------------------------------------------------------------
# 4. generated __init__.py imports and register(ctx) behaves per contract
#    (mirrors PluginManager._load_directory_module + _load_plugin)
# ---------------------------------------------------------------------------


class _FakeCtx:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def register_tool(self, name, toolset, schema, handler, check_fn=None,
                      requires_env=None, is_async=False, description="", emoji=""):
        self.calls.append({
            "name": name, "toolset": toolset, "schema": schema,
            "handler": handler, "check_fn": check_fn, "description": description,
        })


init_file = cap1 / "__init__.py"
spec = importlib.util.spec_from_file_location(
    "hermes_plugins.probe_e0c_toolonly", init_file,
    submodule_search_locations=[str(cap1)],
)
mod = importlib.util.module_from_spec(spec)
sys.modules["hermes_plugins.probe_e0c_toolonly"] = mod
spec.loader.exec_module(mod)

check("4a module has register()", callable(getattr(mod, "register", None)))

ctx = _FakeCtx()
mod.register(ctx)
check("4b register(ctx) registered exactly one tool", len(ctx.calls) == 1)
call = ctx.calls[0] if ctx.calls else {}
check("4c tool name matches", call.get("name") == "probe_lookup")
check("4d toolset namespaced", call.get("toolset") == "plugin-probe-e0c-toolonly")
schema = call.get("schema") or {}
check("4e schema is a valid object schema",
      schema.get("type") == "object" and isinstance(schema.get("properties"), dict))
handler = call.get("handler")
out = handler({"input": "hello"}) if callable(handler) else ""
check("4f handler returns string", isinstance(out, str) and "probe_lookup" in out, str(out)[:120])
check_fn = call.get("check_fn")
check("4g check_fn callable and True", callable(check_fn) and check_fn() is True)
check("4h description propagated", call.get("description") == "Probe lookup tool")
check("4i no template placeholders left",
      "__TOOL_NAME__" not in init_file.read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
# 5. input validation
# ---------------------------------------------------------------------------

res_bad = pmt._plugin_create(name="probe-e0c-bad", tool_template="bad name!")
check("5a invalid tool_template rejected", "error" in res_bad
      and "tool_template" in str(res_bad.get("error", "")))
check("5b invalid tool_template not imported", len(_captured) == 3)

res_bad2 = pmt._plugin_create(name="probe-e0c-bad2", tool_template="ok_tool",
                              skill_name="bad skill!")
check("5c invalid skill_name still rejected", "error" in res_bad2)

# ---------------------------------------------------------------------------
# 6. schema + registry wiring
# ---------------------------------------------------------------------------

props = pmt.PLUGIN_CREATE_SCHEMA.get("properties", {})
check("6a schema has tool_template", "tool_template" in props)
check("6b schema has tool_description", "tool_description" in props)
check("6c required unchanged", pmt.PLUGIN_CREATE_SCHEMA.get("required") == ["name"])

from tools.registry import registry  # noqa: E402

entry = registry.get_entry("plugin_create")
check("6d plugin_create registered", entry is not None)
if entry is not None:
    handler = entry.get("handler") if isinstance(entry, dict) else getattr(entry, "handler", None)
    schema_reg = entry.get("schema") if isinstance(entry, dict) else getattr(entry, "schema", None)
    check("6e registered schema exposes tool_template",
          "tool_template" in (schema_reg or {}).get("properties", {}))
    check("6f handler callable", callable(handler))

# ---------------------------------------------------------------------------
# cleanup + verdict
# ---------------------------------------------------------------------------

for cap in _captured:
    shutil.rmtree(cap["dir"].parent, ignore_errors=True)

print()
if _failures:
    print(f"GAP-E0C PROBE FAILURES: {len(_failures)}")
    for f in _failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL GAP-E0C PROBES PASSED")
