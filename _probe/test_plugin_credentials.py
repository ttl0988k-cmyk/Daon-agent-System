"""Probe: verify plugin credential lifecycle (Credential Store + plugin_set_secret tool).

Checks:
  1. plugin_create with a secrets block is normalized.
  2. plugin_set_secret status reports unset secrets (no value exposed).
  3. plugin_set_secret request registers a pending entry.
  4. set_credential (server-side, UI secure input path) resolves pending + status.
  5. plugin_set_secret remove deletes the credential.
"""
import sys
import traceback

# Windows cp949 console can't encode U+2014 etc.; force UTF-8 for probe output.
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

root = 'c:/daon/Daon agent System'
# Match server.py's sys.path order: [hermes-agent, api/api, api, root]
for p in [root, root + '/api', root + '/api/api', root + '/hermes-agent']:
    if p not in sys.path:
        sys.path.insert(0, p)

print('SYS_PATH_HEAD:', sys.path[:4], flush=True)

from tools.registry import registry, discover_builtin_tools

discover_builtin_tools()
print('PLUGIN_TOOLS:', [n for n in registry.get_all_tool_names() if n.startswith('plugin_')], flush=True)
print('PLUGIN_SET_SECRET_TOOLSET:', registry.get_toolset_for_tool('plugin_set_secret'), flush=True)

PLUGIN_NAME = 'agent_cred_probe'
SECRET_KEY = 'GITHUB_TOKEN'

# Clean up any stale plugin from a previous run
try:
    registry.dispatch('plugin_remove', {'name': PLUGIN_NAME})
except Exception:
    pass

def _cleanup():
    try:
        registry.dispatch('plugin_remove', {'name': PLUGIN_NAME})
    except Exception:
        pass

try:
    # 1. Create a plugin with a secrets block
    r = registry.dispatch('plugin_create', {
        'name': PLUGIN_NAME,
        'description': 'credential probe plugin',
        'author': 'daon-agent',
        'skill_name': 'probe',
        'skill_content': 'Probe skill.',
        'secrets': [{'name': SECRET_KEY, 'description': 'GitHub PAT'}],
    })
    print('CREATE:', r, flush=True)

    # 2. status: should report unset (authenticated False, secret not set) — NO value
    st = registry.dispatch('plugin_set_secret', {'plugin': PLUGIN_NAME, 'action': 'status'})
    print('STATUS_BEFORE:', st, flush=True)

    # 3. request: should register a pending entry (no value in payload)
    req = registry.dispatch('plugin_set_secret', {
        'plugin': PLUGIN_NAME, 'action': 'request', 'key': SECRET_KEY, 'session_id': 'probe-tab',
    })
    print('REQUEST:', req, flush=True)

    import api.plugin_gateway as pg
    pending = pg.list_pending_credentials()
    print('PENDING_AFTER_REQUEST:', pending, flush=True)

    # 4. Simulate UI secure input: set the credential value server-side.
    import api.plugin_credentials as pc
    ok = pc.set_credential(PLUGIN_NAME, SECRET_KEY, 'ghp_FAKE_VALUE_FOR_PROBE')
    print('SET_CREDENTIAL:', ok, flush=True)
    pending2 = pg.list_pending_credentials()
    print('PENDING_AFTER_SET:', pending2, flush=True)

    # status should now be authenticated True
    st2 = registry.dispatch('plugin_set_secret', {'plugin': PLUGIN_NAME, 'action': 'status'})
    print('STATUS_AFTER_SET:', st2, flush=True)

    # 5. remove: delete the stored key, status goes back to unset
    rm = registry.dispatch('plugin_set_secret', {
        'plugin': PLUGIN_NAME, 'action': 'remove', 'key': SECRET_KEY,
    })
    print('REMOVE:', rm, flush=True)
    st3 = registry.dispatch('plugin_set_secret', {'plugin': PLUGIN_NAME, 'action': 'status'})
    print('STATUS_AFTER_REMOVE:', st3, flush=True)

    print('PROBE_DONE: OK', flush=True)
except Exception:
    traceback.print_exc()
    print('PROBE_DONE: FAIL', flush=True)
finally:
    _cleanup()
