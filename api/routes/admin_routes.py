"""
Admin & utility route helpers for Hermes Web UI.
Models, Skills, Cron, Approval, Memory, Auth, Health, Static files.
Extracted from api/routes.py (Phase 2 — Structuring).
"""
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs

from api.config import (
    STATE_DIR, SESSION_DIR, DEFAULT_WORKSPACE, DEFAULT_MODEL,
    SESSIONS, SESSIONS_MAX, LOCK, STREAMS, STREAMS_LOCK, CANCEL_FLAGS,
    SERVER_START_TIME, CLI_TOOLSETS, _INDEX_HTML_PATH,
    IMAGE_EXTS, MD_EXTS, MIME_MAP, MAX_FILE_BYTES, MAX_UPLOAD_BYTES,
    CHAT_LOCK, load_settings, save_settings, get_available_models,
)
from api.helpers import require, bad, j, t, read_body, _security_headers
from api.models import (
    Session, get_session, new_session, all_sessions, title_from,
    _write_session_index, SESSION_INDEX_FILE,
    load_projects, save_projects, import_cli_session,
    get_cli_sessions, get_cli_session_messages,
)
from api.workspace import (
    load_workspaces, save_workspaces, get_last_workspace, set_last_workspace,
    list_dir, read_file_content, safe_resolve_ws,
)

# Approval system (optional -- graceful fallback if agent not available)
try:
    from tools.approval import (
        has_pending, pop_pending, submit_pending,
        approve_session, approve_permanent, save_permanent_allowlist,
        is_approved, _pending, _lock, _permanent_approved,
        resolve_gateway_approval,
    )
except ImportError:
    has_pending = lambda *a, **k: False
    pop_pending = lambda *a, **k: None
    submit_pending = lambda *a, **k: None
    approve_session = lambda *a, **k: None
    approve_permanent = lambda *a, **k: None
    save_permanent_allowlist = lambda *a, **k: None
    is_approved = lambda *a, **k: True
    resolve_gateway_approval = lambda *a, **k: 0
    _pending = {}
    _lock = threading.Lock()
    _permanent_approved = set()


# ── Login page (self-contained, no external deps) ────────────────────────────
_LOGIN_PAGE_HTML = '''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hermes — Sign in</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#1a1a2e;color:#e8e8f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#16213e;border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:36px 32px;
  width:320px;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.3)}
.logo{width:48px;height:48px;border-radius:12px;background:linear-gradient(145deg,#e8a030,#e94560);
  display:flex;align-items:center;justify-content:center;font-weight:800;font-size:20px;color:#fff;
  margin:0 auto 12px;box-shadow:0 2px 12px rgba(233,69,96,.3)}
h1{font-size:18px;font-weight:600;margin-bottom:4px}
.sub{font-size:12px;color:#8888aa;margin-bottom:24px}
input{width:100%;padding:10px 14px;border-radius:10px;border:1px solid rgba(255,255,255,.1);
  background:rgba(255,255,255,.04);color:#e8e8f0;font-size:14px;outline:none;margin-bottom:14px;
  transition:border-color .15s}
input:focus{border-color:rgba(124,185,255,.5);box-shadow:0 0 0 3px rgba(124,185,255,.1)}
button{width:100%;padding:10px;border-radius:10px;border:none;background:rgba(124,185,255,.15);
  border:1px solid rgba(124,185,255,.3);color:#7cb9ff;font-size:14px;font-weight:600;cursor:pointer;
  transition:all .15s}
button:hover{background:rgba(124,185,255,.25)}
.err{color:#e94560;font-size:12px;margin-top:10px;display:none}
</style></head><body>
<div class="card">
  <div class="logo">H</div>
  <h1>Hermes</h1>
  <p class="sub">Enter your password to continue</p>
  <form onsubmit="return doLogin(event)">
    <input type="password" id="pw" placeholder="Password" autofocus>
    <button type="submit">Sign in</button>
  </form>
  <div class="err" id="err"></div>
</div>
<script>
async function doLogin(e){
  e.preventDefault();
  const pw=document.getElementById('pw').value;
  const err=document.getElementById('err');
  err.style.display='none';
  try{
    const res=await fetch('/api/auth/login',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({password:pw}),credentials:'include'});
    const data=await res.json();
    if(res.ok&&data.ok){window.location.href='/';}
    else{err.textContent=data.error||'Invalid password';err.style.display='block';}
  }catch(ex){err.textContent='Connection failed';err.style.display='block';}
}
</script></body></html>'''


# ── GET route helpers ─────────────────────────────────────────────────────────

def handle_get_index(handler, parsed) -> bool:
    """GET / or /index.html — serve the main index page."""
    return t(handler, _INDEX_HTML_PATH.read_text(encoding='utf-8'),
             content_type='text/html; charset=utf-8')


def handle_get_login(handler, parsed) -> bool:
    """GET /login — serve the login page."""
    return t(handler, _LOGIN_PAGE_HTML, content_type='text/html; charset=utf-8')


def handle_get_auth_status(handler, parsed) -> bool:
    """GET /api/auth/status — check authentication status."""
    from api.auth import is_auth_enabled, parse_cookie, verify_session
    logged_in = False
    if is_auth_enabled():
        cv = parse_cookie(handler)
        logged_in = bool(cv and verify_session(cv))
    return j(handler, {'auth_enabled': is_auth_enabled(), 'logged_in': logged_in})


def handle_get_favicon(handler, parsed) -> bool:
    """GET /favicon.ico — return 204 No Content."""
    handler.send_response(204)
    handler.end_headers()
    return True


def handle_get_health(handler, parsed) -> bool:
    """GET /health — server health check."""
    with STREAMS_LOCK:
        n_streams = len(STREAMS)
    return j(handler, {
        'status': 'ok', 'sessions': len(SESSIONS),
        'active_streams': n_streams,
        'uptime_seconds': round(time.time() - SERVER_START_TIME, 1),
    })


def handle_get_models(handler, parsed) -> bool:
    """GET /api/models — return available models."""
    return j(handler, get_available_models())


def handle_serve_static(handler, parsed) -> bool:
    """GET /static/* — serve static files."""
    from api.config import RESOURCE_DIR
    static_root = (RESOURCE_DIR / 'static').resolve()
    rel = parsed.path[len('/static/'):]
    static_file = (static_root / rel).resolve()
    try:
        static_file.relative_to(static_root)
    except ValueError:
        return j(handler, {'error': 'not found'}, status=404)
    if not static_file.exists() or not static_file.is_file():
        return j(handler, {'error': 'not found'}, status=404)
    ext = static_file.suffix.lower()
    ct = {'css': 'text/css', 'js': 'application/javascript',
          'html': 'text/html'}.get(ext.lstrip('.'), 'text/plain')
    handler.send_response(200)
    handler.send_header('Content-Type', f'{ct}; charset=utf-8')
    handler.send_header('Cache-Control', 'no-store')
    raw = static_file.read_bytes()
    handler.send_header('Content-Length', str(len(raw)))
    handler.end_headers()
    try:
        handler.wfile.write(raw)
    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
        pass  # Client disconnected before response could be sent
    return True


# ── Approval (GET) ────────────────────────────────────────────────────────────

def handle_get_approval_pending(handler, parsed) -> bool:
    """GET /api/approval/pending — check for pending approval."""
    sid = parse_qs(parsed.query).get('session_id', [''])[0]
    if has_pending(sid):
        with _lock:
            p = dict(_pending.get(sid, {}))
        return j(handler, {'pending': p})
    return j(handler, {'pending': None})


def handle_get_approval_inject(handler, parsed) -> bool:
    """GET /api/approval/inject_test — inject a fake pending approval (loopback-only)."""
    if handler.client_address[0] != '127.0.0.1':
        return j(handler, {'error': 'not found'}, status=404)
    qs = parse_qs(parsed.query)
    sid = qs.get('session_id', [''])[0]
    key = qs.get('pattern_key', ['test_pattern'])[0]
    cmd = qs.get('command', ['rm -rf /tmp/test'])[0]
    if sid:
        submit_pending(sid, {
            'command': cmd, 'pattern_key': key,
            'pattern_keys': [key], 'description': 'test pattern',
        })
        return j(handler, {'ok': True, 'session_id': sid})
    return j(handler, {'error': 'session_id required'}, status=400)


# ── Cron (GET) ────────────────────────────────────────────────────────────────

def handle_get_crons(handler, parsed) -> bool:
    """GET /api/crons — list all cron jobs."""
    from cron.jobs import list_jobs
    return j(handler, {'jobs': list_jobs(include_disabled=True)})


def handle_get_cron_output(handler, parsed) -> bool:
    """GET /api/crons/output — get cron job output."""
    from cron.jobs import OUTPUT_DIR as CRON_OUT
    qs = parse_qs(parsed.query)
    job_id = qs.get('job_id', [''])[0]
    limit = int(qs.get('limit', ['5'])[0])
    if not job_id:
        return j(handler, {'error': 'job_id required'}, status=400)
    out_dir = CRON_OUT / job_id
    outputs = []
    if out_dir.exists():
        files = sorted(out_dir.glob('*.md'), reverse=True)[:limit]
        for f in files:
            try:
                txt = f.read_text(encoding='utf-8', errors='replace')
                outputs.append({'filename': f.name, 'content': txt[:8000]})
            except Exception:
                pass
    return j(handler, {'job_id': job_id, 'outputs': outputs})


def handle_get_cron_recent(handler, parsed) -> bool:
    """GET /api/crons/recent — return cron jobs completed since a timestamp."""
    import datetime
    qs = parse_qs(parsed.query)
    since = float(qs.get('since', ['0'])[0])
    try:
        from cron.jobs import list_jobs
        jobs = list_jobs(include_disabled=True)
        completions = []
        for job in jobs:
            last_run = job.get('last_run_at')
            if not last_run:
                continue
            if isinstance(last_run, str):
                try:
                    ts = datetime.datetime.fromisoformat(last_run.replace('Z', '+00:00')).timestamp()
                except (ValueError, TypeError):
                    continue
            else:
                ts = float(last_run)
            if ts > since:
                completions.append({
                    'job_id': job.get('id', ''),
                    'name': job.get('name', 'Unknown'),
                    'status': job.get('last_status', 'unknown'),
                    'completed_at': ts,
                })
        return j(handler, {'completions': completions, 'since': since})
    except ImportError:
        return j(handler, {'completions': [], 'since': since})


# ── Skills (GET) ──────────────────────────────────────────────────────────────

def handle_get_skills(handler, parsed) -> bool:
    """GET /api/skills — list all skills (curated + auto-distilled with lifecycle)."""
    from tools.skills_tool import skills_list as _skills_list, _parse_frontmatter
    from pathlib import Path as _Path
    raw = _skills_list()
    data = json.loads(raw) if isinstance(raw, str) else raw
    skills = data.get('skills', [])

    # ── Merge global + profile auto-distilled skills (profile dir may differ from global dir) ──
    try:
        from api.skill_registry import get_skill_registry, _get_all_auto_skills_dirs
        registry = get_skill_registry()

        seen_names = set(s.get('name', '') for s in skills)

        # Collect all manifests from all auto-skills directories
        all_manifests: dict[str, dict] = {}
        for auto_dir in _get_all_auto_skills_dirs():
            if auto_dir.exists():
                manifest = registry._load_manifest(auto_dir)
                all_manifests.update(manifest)

        # Scan all auto-skills directories for SKILL.md files
        for auto_dir in _get_all_auto_skills_dirs():
            if not auto_dir.exists():
                continue
            for skill_md in auto_dir.rglob("SKILL.md"):
                try:
                    content = skill_md.read_text(encoding="utf-8")[:4000]
                    frontmatter, body = _parse_frontmatter(content)
                    from agent.skill_utils import skill_matches_platform
                    if not skill_matches_platform(frontmatter):
                        continue
                    name = frontmatter.get("name", skill_md.parent.name)
                    if name in seen_names:
                        continue
                    seen_names.add(name)
                    description = frontmatter.get("description", "") or frontmatter.get("purpose", "")
                    if not description:
                        for line in body.strip().split("\n"):
                            line = line.strip()
                            if line and not line.startswith("#"):
                                description = line[:256]
                                break
                    name_key = skill_md.parent.name
                    lifecycle = all_manifests.get(name_key, {}).get('status', 'draft')
                    skills.append({
                        "name": name,
                        "description": description,
                        "category": "auto",
                        "lifecycle": lifecycle,
                        "source": "auto",
                    })
                except Exception:
                    continue

        # ── Inject lifecycle for auto skills already in the list ──
        for s in skills:
            name_key = s.get('name', '').strip().lower().replace(' ', '-')
            if name_key in all_manifests:
                s.setdefault('lifecycle', all_manifests[name_key].get('status', 'draft'))
                s.setdefault('source', 'auto')
    except Exception as e:
        _logger = logging.getLogger(__name__)
        _logger.warning("[skills] Failed to merge auto skills: %s", e, exc_info=True)

    return j(handler, {'skills': skills})


def handle_get_skill_content(handler, parsed) -> bool:
    """GET /api/skills/content — get skill content or linked file."""
    from tools.skills_tool import skill_view as _skill_view, SKILLS_DIR
    qs = parse_qs(parsed.query)
    name = qs.get('name', [''])[0]
    if not name:
        return j(handler, {'error': 'name required'}, status=400)
    file_path = qs.get('file', [''])[0]
    if file_path:
        import re as _re
        if _re.search(r'[*?\[\]]', name):
            return bad(handler, 'Invalid skill name', 400)
        skill_dir = None
        for p in SKILLS_DIR.rglob(name):
            if p.is_dir():
                skill_dir = p
                break
        if not skill_dir:
            return bad(handler, 'Skill not found', 404)
        target = (skill_dir / file_path).resolve()
        try:
            target.relative_to(skill_dir.resolve())
        except ValueError:
            return bad(handler, 'Invalid file path', 400)
        if not target.exists() or not target.is_file():
            return bad(handler, 'File not found', 404)
        return j(handler, {'content': target.read_text(encoding='utf-8'), 'path': file_path})
    raw = _skill_view(name)
    data = json.loads(raw) if isinstance(raw, str) else raw
    if 'linked_files' not in data:
        data['linked_files'] = {}
    return j(handler, data)


# ── Memory (GET) ──────────────────────────────────────────────────────────────

def handle_get_memory(handler, parsed) -> bool:
    """GET /api/memory — read memory and user profile."""
    try:
        from api.profiles import get_active_hermes_home
        mem_dir = get_active_hermes_home() / 'memories'
    except ImportError:
        mem_dir = Path.home() / '.hermes' / 'memories'
    mem_file = mem_dir / 'MEMORY.md'
    user_file = mem_dir / 'USER.md'
    memory = mem_file.read_text(encoding='utf-8', errors='replace') if mem_file.exists() else ''
    user = user_file.read_text(encoding='utf-8', errors='replace') if user_file.exists() else ''
    return j(handler, {
        'memory': memory, 'user': user,
        'memory_path': str(mem_file), 'user_path': str(user_file),
        'memory_mtime': mem_file.stat().st_mtime if mem_file.exists() else None,
        'user_mtime': user_file.stat().st_mtime if user_file.exists() else None,
    })


# ── POST route helpers ────────────────────────────────────────────────────────

# ── Approval (POST) ──

def handle_post_approval_respond(handler, body) -> bool:
    """POST /api/approval/respond — respond to a pending approval."""
    sid = body.get('session_id', '')
    if not sid:
        return bad(handler, 'session_id is required')
    choice = body.get('choice', 'deny')
    if choice not in ('once', 'session', 'always', 'deny'):
        return bad(handler, f'Invalid choice: {choice}')
    
    # Resolve the blocked gateway thread if any
    resolve_gateway_approval(sid, choice)
    
    with _lock:
        pending = _pending.pop(sid, None)
    if pending:
        keys = pending.get('pattern_keys') or [pending.get('pattern_key', '')]
        if choice in ('once', 'session'):
            for k in keys:
                approve_session(sid, k)
        elif choice == 'always':
            for k in keys:
                approve_session(sid, k)
                approve_permanent(k)
            save_permanent_allowlist(_permanent_approved)
    return j(handler, {'ok': True, 'choice': choice})


# ── Skills (POST) ──

def handle_post_skill_promote(handler, body) -> bool:
    """POST /api/skills/promote — promote a DRAFT auto-skill to APPROVED (or other lifecycle status)."""
    try:
        require(body, 'name')
    except ValueError as e:
        return bad(handler, str(e))

    skill_name = body['name'].strip().lower().replace(' ', '-')
    to_status = body.get('to_status', 'approved')

    if to_status not in ('approved', 'review', 'draft', 'rejected'):
        return bad(handler, f'Invalid status: {to_status}')

    try:
        from api.skill_registry import get_skill_registry
        registry = get_skill_registry()
        success = registry.promote_skill(skill_name, to_status=to_status)
        if success:
            registry.reload()
            return j(handler, {
                'ok': True,
                'name': skill_name,
                'lifecycle': to_status,
                'message': f'Skill "{skill_name}" promoted to {to_status}.',
            })
        else:
            return bad(handler, f'Skill "{skill_name}" not found or not an auto-distilled skill.', 404)
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).exception("[skills/promote] Failed: %s", e)
        return bad(handler, str(e), 500)


def handle_post_skill_reject(handler, body) -> bool:
    """POST /api/skills/reject — reject (mark as rejected) a DRAFT auto-skill."""
    try:
        require(body, 'name')
    except ValueError as e:
        return bad(handler, str(e))

    skill_name = body['name'].strip().lower().replace(' ', '-')

    try:
        from api.skill_registry import get_skill_registry
        registry = get_skill_registry()
        success = registry.reject_skill(skill_name)
        if success:
            registry.reload()
            return j(handler, {
                'ok': True,
                'name': skill_name,
                'lifecycle': 'rejected',
                'message': f'Skill "{skill_name}" rejected.',
            })
        else:
            return bad(handler, f'Skill "{skill_name}" not found or not an auto-distilled skill.', 404)
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).exception("[skills/reject] Failed: %s", e)
        return bad(handler, str(e), 500)


def handle_post_skill_save(handler, body) -> bool:
    """POST /api/skills/save — save a skill."""
    try:
        require(body, 'name', 'content')
    except ValueError as e:
        return bad(handler, str(e))
    skill_name = body['name'].strip().lower().replace(' ', '-')
    if not skill_name or '/' in skill_name or '..' in skill_name:
        return bad(handler, 'Invalid skill name')
    category = body.get('category', '').strip()
    if category and ('/' in category or '..' in category):
        return bad(handler, 'Invalid category')
    from tools.skills_tool import SKILLS_DIR
    if category:
        skill_dir = SKILLS_DIR / category / skill_name
    else:
        skill_dir = SKILLS_DIR / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / 'SKILL.md'
    skill_file.write_text(body['content'], encoding='utf-8')
    return j(handler, {'ok': True, 'name': skill_name, 'path': str(skill_file)})


def handle_post_skill_delete(handler, body) -> bool:
    """POST /api/skills/delete — delete a skill."""
    try:
        require(body, 'name')
    except ValueError as e:
        return bad(handler, str(e))
    from tools.skills_tool import SKILLS_DIR
    import shutil
    matches = list(SKILLS_DIR.rglob(f'{body["name"]}/SKILL.md'))
    if not matches:
        return bad(handler, 'Skill not found', 404)
    skill_dir = matches[0].parent
    shutil.rmtree(str(skill_dir))
    return j(handler, {'ok': True, 'name': body['name']})


# ── Memory (POST) ──

def handle_post_memory_write(handler, body) -> bool:
    """POST /api/memory/write — write memory or user profile."""
    try:
        require(body, 'section', 'content')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        from api.profiles import get_active_hermes_home
        mem_dir = get_active_hermes_home() / 'memories'
    except ImportError:
        mem_dir = Path.home() / '.hermes' / 'memories'
    mem_dir.mkdir(parents=True, exist_ok=True)
    section = body['section']
    if section == 'memory':
        target = mem_dir / 'MEMORY.md'
    elif section == 'user':
        target = mem_dir / 'USER.md'
    else:
        return bad(handler, 'section must be "memory" or "user"')
    target.write_text(body['content'], encoding='utf-8')
    return j(handler, {'ok': True, 'section': section, 'path': str(target)})


# ── Cron (POST) ──

def handle_post_cron_create(handler, body) -> bool:
    """POST /api/crons/create — create a cron job."""
    try:
        require(body, 'prompt', 'schedule')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        from cron.jobs import create_job
        job = create_job(
            prompt=body['prompt'], schedule=body['schedule'],
            name=body.get('name') or None, deliver=body.get('deliver') or 'local',
            skills=body.get('skills') or [], model=body.get('model') or None,
        )
        return j(handler, {'ok': True, 'job': job})
    except Exception as e:
        return j(handler, {'error': str(e)}, status=400)


def handle_post_cron_update(handler, body) -> bool:
    """POST /api/crons/update — update a cron job."""
    try:
        require(body, 'job_id')
    except ValueError as e:
        return bad(handler, str(e))
    from cron.jobs import update_job
    updates = {k: v for k, v in body.items() if k != 'job_id' and v is not None}
    job = update_job(body['job_id'], updates)
    if not job:
        return bad(handler, 'Job not found', 404)
    return j(handler, {'ok': True, 'job': job})


def handle_post_cron_delete(handler, body) -> bool:
    """POST /api/crons/delete — delete a cron job."""
    try:
        require(body, 'job_id')
    except ValueError as e:
        return bad(handler, str(e))
    from cron.jobs import remove_job
    ok = remove_job(body['job_id'])
    if not ok:
        return bad(handler, 'Job not found', 404)
    return j(handler, {'ok': True, 'job_id': body['job_id']})


def handle_post_cron_run(handler, body) -> bool:
    """POST /api/crons/run — trigger a cron job manually."""
    job_id = body.get('job_id', '')
    if not job_id:
        return bad(handler, 'job_id required')
    from cron.jobs import get_job
    job = get_job(job_id)
    if not job:
        return bad(handler, 'Job not found', 404)

    def _run_and_save():
        try:
            from cron.scheduler import run_job
            from cron.jobs import save_job_output, mark_job_run
            success, output, final_response, error = run_job(job)
            save_job_output(job['id'], output)
            if success and not final_response:
                success = False
                error = "Agent completed but produced empty response"
            mark_job_run(job['id'], success, error)
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception(f"Manual cron execution failed for job {job_id}: {e}")

    threading.Thread(target=_run_and_save, daemon=True).start()
    return j(handler, {'ok': True, 'job_id': job_id, 'status': 'triggered'})


def handle_post_cron_pause(handler, body) -> bool:
    """POST /api/crons/pause — pause a cron job."""
    job_id = body.get('job_id', '')
    if not job_id:
        return bad(handler, 'job_id required')
    from cron.jobs import pause_job
    result = pause_job(job_id, reason=body.get('reason'))
    if result:
        return j(handler, {'ok': True, 'job': result})
    return bad(handler, 'Job not found', 404)


def handle_post_cron_resume(handler, body) -> bool:
    """POST /api/crons/resume — resume a paused cron job."""
    job_id = body.get('job_id', '')
    if not job_id:
        return bad(handler, 'job_id required')
    from cron.jobs import resume_job
    result = resume_job(job_id)
    if result:
        return j(handler, {'ok': True, 'job': result})
    return bad(handler, 'Job not found', 404)


# ── Auth (POST) ──

def handle_post_auth_login(handler, body) -> bool:
    """POST /api/auth/login — authenticate."""
    from api.auth import verify_password, create_session, set_auth_cookie, is_auth_enabled
    if not is_auth_enabled():
        return j(handler, {'ok': True, 'message': 'Auth not enabled'})
    password = body.get('password', '')
    if not verify_password(password):
        return bad(handler, 'Invalid password', 401)
    cookie_val = create_session()
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Cache-Control', 'no-store')
    _security_headers(handler)
    set_auth_cookie(handler, cookie_val)
    handler.end_headers()
    try:
        handler.wfile.write(json.dumps({'ok': True}).encode())
    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
        pass  # Client disconnected before response could be sent
    return True


def handle_post_auth_logout(handler, body) -> bool:
    """POST /api/auth/logout — invalidate session."""
    from api.auth import clear_auth_cookie, invalidate_session, parse_cookie
    cookie_val = parse_cookie(handler)
    if cookie_val:
        invalidate_session(cookie_val)
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Cache-Control', 'no-store')
    _security_headers(handler)
    clear_auth_cookie(handler)
    handler.end_headers()
    try:
        handler.wfile.write(json.dumps({'ok': True}).encode())
    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
        pass  # Client disconnected before response could be sent
    return True
