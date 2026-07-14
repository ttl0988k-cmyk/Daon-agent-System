"""
Git automation route helpers for Hermes Web UI.
Provides commit, push, pull, diff, log endpoints for session workspaces.
"""
import json
import re
from pathlib import Path
from urllib.parse import parse_qs

from api.helpers import require, bad, j, safe_resolve
from api.models import get_session
from api.workspace import _run_git, git_info_for_workspace


# ── Helpers ──────────────────────────────────────────────────────────────────

def _resolve_workspace(body_or_qs: dict, is_get: bool = False) -> Path:
    """Resolve a workspace Path from session_id or direct workspace path."""
    sid = body_or_qs.get('session_id', '')
    if sid:
        try:
            s = get_session(sid)
            return Path(s.workspace)
        except KeyError:
            raise ValueError('Session not found')
    ws = body_or_qs.get('workspace', '')
    if ws:
        p = Path(ws).resolve()
        if not p.exists():
            raise ValueError(f'Workspace not found: {ws}')
        return p
    raise ValueError('session_id or workspace required')


def _require_git(workspace: Path):
    """Raise ValueError if not a git repo."""
    if not (workspace / '.git').exists():
        raise ValueError('Not a git repository')


def _sanitize_diff(diff_text: str, max_lines: int = 500) -> str:
    """Truncate overly large diffs to prevent UI overload."""
    lines = diff_text.splitlines()
    if len(lines) > max_lines:
        return '\n'.join(lines[:max_lines]) + f'\n\n... ({len(lines) - max_lines} more lines truncated)'
    return diff_text


# ── GET route helpers ────────────────────────────────────────────────────────

def handle_get_git_status(handler, parsed) -> bool:
    """GET /api/git/status — extended git info including recent log."""
    qs = parse_qs(parsed.query)
    sid = (qs.get('session_id', [''])[0]) if isinstance(qs.get('session_id'), list) else qs.get('session_id', '')
    wsp = (qs.get('workspace', [''])[0]) if isinstance(qs.get('workspace'), list) else qs.get('workspace', '')
    try:
        ws = _resolve_workspace({'session_id': sid, 'workspace': wsp}, is_get=True)
    except ValueError as e:
        return bad(handler, str(e))
    _require_git(ws)
    info = git_info_for_workspace(ws)
    if info is None:
        return j(handler, {'git': None})
    # Recent log (last 10 commits)
    log_raw = _run_git(['log', '--oneline', '-10', '--decorate'], ws) or ''
    log_entries = []
    for line in log_raw.splitlines():
        if line.strip():
            log_entries.append(line.strip())
    info['recent_log'] = log_entries
    return j(handler, {'git': info})


def handle_get_git_diff(handler, parsed) -> bool:
    """GET /api/git/diff — get working-tree diff."""
    qs = parse_qs(parsed.query)
    sid = (qs.get('session_id', [''])[0]) if isinstance(qs.get('session_id'), list) else qs.get('session_id', '')
    wsp = (qs.get('workspace', [''])[0]) if isinstance(qs.get('workspace'), list) else qs.get('workspace', '')
    staged = qs.get('staged', ['0'])[0] == '1'
    try:
        ws = _resolve_workspace({'session_id': sid, 'workspace': wsp}, is_get=True)
    except ValueError as e:
        return bad(handler, str(e))
    _require_git(ws)
    args = ['diff']
    if staged:
        args.append('--staged')
    raw = _run_git(args, ws) or ''
    return j(handler, {'diff': _sanitize_diff(raw)})


def handle_get_git_log(handler, parsed) -> bool:
    """GET /api/git/log — get detailed git log."""
    qs = parse_qs(parsed.query)
    sid = (qs.get('session_id', [''])[0]) if isinstance(qs.get('session_id'), list) else qs.get('session_id', '')
    wsp = (qs.get('workspace', [''])[0]) if isinstance(qs.get('workspace'), list) else qs.get('workspace', '')
    limit = int(qs.get('limit', ['20'])[0])
    try:
        ws = _resolve_workspace({'session_id': sid, 'workspace': wsp}, is_get=True)
    except ValueError as e:
        return bad(handler, str(e))
    _require_git(ws)
    # Pretty format: hash|author|date|message
    fmt = '--format=%h|%an|%ar|%s'
    raw = _run_git(['log', fmt, f'-{min(limit, 50)}'], ws) or ''
    entries = []
    for line in raw.splitlines():
        parts = line.split('|', 3)
        if len(parts) >= 4:
            entries.append({
                'hash': parts[0],
                'author': parts[1],
                'date': parts[2],
                'message': parts[3],
            })
    return j(handler, {'log': entries})


def handle_get_git_conflict(handler, parsed) -> bool:
    """GET /api/git/conflicts — list conflicted files."""
    qs = parse_qs(parsed.query)
    sid = (qs.get('session_id', [''])[0]) if isinstance(qs.get('session_id'), list) else qs.get('session_id', '')
    wsp = (qs.get('workspace', [''])[0]) if isinstance(qs.get('workspace'), list) else qs.get('workspace', '')
    try:
        ws = _resolve_workspace({'session_id': sid, 'workspace': wsp}, is_get=True)
    except ValueError as e:
        return bad(handler, str(e))
    _require_git(ws)
    raw = _run_git(['diff', '--name-only', '--diff-filter=U'], ws) or ''
    conflicts = [f for f in raw.splitlines() if f.strip()]
    return j(handler, {'conflicts': conflicts})


# ── POST route helpers ───────────────────────────────────────────────────────

def handle_post_git_commit(handler, body) -> bool:
    """POST /api/git/commit — stage all and commit with message."""
    try:
        require(body, 'message')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        ws = _resolve_workspace(body)
    except ValueError as e:
        return bad(handler, str(e), 404)
    _require_git(ws)
    message = body['message'].strip()
    if not message:
        return bad(handler, 'Commit message cannot be empty')
    # Stage all
    stage_out = _run_git(['add', '-A'], ws)
    if stage_out is False:
        return bad(handler, 'git add failed (timeout or error)')
    # Commit
    commit_out = _run_git(['commit', '-m', message], ws, timeout=10)
    if commit_out is None:
        return bad(handler, 'git commit failed')
    # Check if nothing to commit
    if 'nothing to commit' in commit_out.lower() or 'nothing added' in commit_out.lower():
        return j(handler, {'ok': True, 'message': commit_out.strip(), 'empty': True})
    # Return post-commit status
    info = git_info_for_workspace(ws)
    return j(handler, {'ok': True, 'message': commit_out.strip(), 'git': info})


def handle_post_git_push(handler, body) -> bool:
    """POST /api/git/push — push to origin."""
    try:
        ws = _resolve_workspace(body)
    except ValueError as e:
        return bad(handler, str(e), 404)
    _require_git(ws)
    force = body.get('force', False)
    args = ['push']
    if force:
        args.append('--force')
    out = _run_git(args, ws, timeout=30)
    if out is None:
        return bad(handler, 'git push failed (timeout or auth error)')
    info = git_info_for_workspace(ws)
    return j(handler, {'ok': True, 'message': out.strip(), 'git': info})


def handle_post_git_pull(handler, body) -> bool:
    """POST /api/git/pull — pull from origin."""
    try:
        ws = _resolve_workspace(body)
    except ValueError as e:
        return bad(handler, str(e), 404)
    _require_git(ws)
    rebase = body.get('rebase', False)
    args = ['pull']
    if rebase:
        args.append('--rebase')
    out = _run_git(args, ws, timeout=30)
    if out is None:
        return bad(handler, 'git pull failed (timeout or auth error)')
    # Check for merge conflicts
    conflicts = []
    if 'CONFLICT' in (out or ''):
        conflict_raw = _run_git(['diff', '--name-only', '--diff-filter=U'], ws) or ''
        conflicts = [f for f in conflict_raw.splitlines() if f.strip()]
    info = git_info_for_workspace(ws)
    return j(handler, {
        'ok': True,
        'message': out.strip() if out else '',
        'git': info,
        'conflicts': conflicts,
    })


def handle_post_git_stage(handler, body) -> bool:
    """POST /api/git/stage — stage specific files."""
    try:
        require(body, 'files')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        ws = _resolve_workspace(body)
    except ValueError as e:
        return bad(handler, str(e), 404)
    _require_git(ws)
    files = body['files']
    if isinstance(files, str):
        files = [files]
    if not files:
        return bad(handler, 'No files specified')
    # Validate paths are within workspace
    for f in files:
        try:
            safe_resolve(ws, f)
        except ValueError:
            return bad(handler, f'Invalid path: {f}')
    out = _run_git(['add'] + files, ws)
    if out is None:
        return bad(handler, 'git add failed')
    return j(handler, {'ok': True, 'staged': files})


def handle_post_git_unstage(handler, body) -> bool:
    """POST /api/git/unstage — unstage files."""
    try:
        require(body, 'files')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        ws = _resolve_workspace(body)
    except ValueError as e:
        return bad(handler, str(e), 404)
    _require_git(ws)
    files = body['files']
    if isinstance(files, str):
        files = [files]
    out = _run_git(['reset', 'HEAD'] + files, ws)
    if out is None:
        return bad(handler, 'git reset failed')
    return j(handler, {'ok': True, 'unstaged': files})


def handle_post_git_discard(handler, body) -> bool:
    """POST /api/git/discard — discard changes to a file (git checkout -- <file>)."""
    try:
        require(body, 'path')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        ws = _resolve_workspace(body)
    except ValueError as e:
        return bad(handler, str(e), 404)
    _require_git(ws)
    try:
        safe_resolve(ws, body['path'])
    except ValueError:
        return bad(handler, f'Invalid path: {body["path"]}')
    out = _run_git(['checkout', '--', body['path']], ws)
    if out is None:
        return bad(handler, 'git checkout failed')
    return j(handler, {'ok': True, 'discarded': body['path']})
