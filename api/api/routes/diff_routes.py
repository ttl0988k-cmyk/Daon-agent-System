"""
Diff-based code editing, preview, checkpoint route helpers.
Implements Roo Code-style apply_diff with preview-before-apply workflow.

Diff format (same as Roo Code):
  <<<<<<< SEARCH
  :start_line:[line_number]
  -------
  [exact content to find]
  =======
  [new content to replace with]
  >>>>>>> REPLACE

Flow: AI -> Preview -> Monaco Diff -> User Approval -> Apply -> Checkpoint
"""
import json as _json
import re
import threading
import time
import uuid
from pathlib import Path

from api.helpers import require, bad, j, safe_resolve
from api.models import get_session

from api.config import STATE_DIR

_STATE_DIR = STATE_DIR

# ── Checkpoint storage ────────────────────────────────────────────────────────

_checkpoints_lock = threading.Lock()

# ── Diff Preview storage (in-memory, per-session) ─────────────────────────────

_previews_lock = threading.Lock()
_diff_previews = {}  # preview_id -> {session_id, path, blocks, original, new_content, created_at, line_changes}

# ── Change History (in-memory, per-session, ring buffer) ──────────────────────

_history_lock = threading.Lock()
_change_history = {}  # session_id -> list[history_entry]
_MAX_HISTORY_PER_SESSION = 50


def _checkpoints_dir() -> Path:
    d = _STATE_DIR / 'checkpoints'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_checkpoint_dir(session_id: str) -> Path:
    d = _checkpoints_dir() / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _create_checkpoint(session_id: str, file_path: str, content: str) -> str:
    """Create a checkpoint of a file before modification. Returns checkpoint ID."""
    cp_dir = _session_checkpoint_dir(session_id)
    cp_id = uuid.uuid4().hex[:12]
    cp_file = cp_dir / f"{cp_id}.json"
    cp_file.write_text(_json.dumps({
        'id': cp_id,
        'file_path': file_path,
        'content': content,
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }), encoding='utf-8')
    return cp_id


def _add_change_history(session_id: str, entry: dict) -> None:
    """Add a change history entry with ring-buffer semantics."""
    with _history_lock:
        hist = _change_history.setdefault(session_id, [])
        hist.insert(0, entry)
        if len(hist) > _MAX_HISTORY_PER_SESSION:
            hist[:] = hist[:_MAX_HISTORY_PER_SESSION]


# ── Diff parser ───────────────────────────────────────────────────────────────

_DIFF_BLOCK_RE = re.compile(
    r'<<<<<<< SEARCH\n'
    r':start_line:(\d+)\n'
    r'-------\n'
    r'(.*?)\n'
    r'=======\n'
    r'(.*?)\n'
    r'>>>>>>> REPLACE',
    re.DOTALL
)


def _parse_diff_blocks(diff_text: str) -> list[dict]:
    """Parse a diff string into search/replace blocks."""
    blocks = []
    for m in _DIFF_BLOCK_RE.finditer(diff_text):
        blocks.append({
            'start_line': int(m.group(1)),
            'search': m.group(2),
            'replace': m.group(3),
        })
    return blocks


def _apply_diff_blocks(content: str, blocks: list[dict]) -> tuple[str, list[str]]:
    """Apply search/replace blocks to content. Returns (new_content, errors).

    Blocks are applied in reverse order to preserve line number integrity.
    Each SEARCH block must match exactly once.
    """
    errors = []
    for block in reversed(blocks):
        search = block['search']
        replace = block['replace']
        start_line = block['start_line']
        if search not in content:
            errors.append(f"Block start_line={start_line}: SEARCH text not found in file")
            continue
        count = content.count(search)
        if count > 1:
            errors.append(
                f"Block start_line={start_line}: SEARCH text matches {count} times (ambiguous)"
            )
            continue
        content = content.replace(search, replace, 1)
    return content, errors


def _compute_line_changes(original: str, new: str) -> dict:
    """Compute simple line change statistics between original and new content."""
    orig_lines = original.split('\n')
    new_lines = new.split('\n')
    # Simple diff: count added/removed lines by longest common subsequence approximation
    added = max(0, len(new_lines) - len(orig_lines))
    removed = max(0, len(orig_lines) - len(new_lines))
    # Build unified diff for display
    diff_lines = []
    for i, line in enumerate(new_lines):
        if i < len(orig_lines):
            if line != orig_lines[i]:
                diff_lines.append({'type': 'changed', 'old': orig_lines[i], 'new': line, 'line': i + 1})
        else:
            diff_lines.append({'type': 'added', 'new': line, 'line': i + 1})
    for i in range(len(new_lines), len(orig_lines)):
        diff_lines.append({'type': 'removed', 'old': orig_lines[i], 'line': i + 1})
    return {
        'added': added,
        'removed': removed,
        'total_old_lines': len(orig_lines),
        'total_new_lines': len(new_lines),
        'changes': diff_lines[:200],  # cap at 200 for display
    }


# ── POST /api/file/preview-diff ───────────────────────────────────────────────

def handle_post_file_preview_diff(handler, body) -> bool:
    """POST /api/file/preview-diff — preview diff blocks without applying.

    Body: { session_id, path, diff, source_agent? (e.g. 'coder', 'architect') }
    Returns: { ok, preview_id, path, blocks_count, original_snippet, new_snippet, line_changes }
    """
    try:
        require(body, 'session_id', 'path', 'diff')
    except ValueError as e:
        return bad(handler, str(e))

    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)

    try:
        target = safe_resolve(Path(s.workspace), body['path'])
        if not target.exists():
            return bad(handler, 'File not found', 404)
        if target.is_dir():
            return bad(handler, 'Cannot diff a directory')

        original_content = target.read_text(encoding='utf-8')

        blocks = _parse_diff_blocks(body['diff'])

        if not blocks:
            return bad(handler, 'No valid diff blocks found. Use the SEARCH/REPLACE format.')

        new_content, errors = _apply_diff_blocks(original_content, list(blocks))

        if errors:
            return j(handler, {
                'ok': False,
                'error': 'Diff preview failed',
                'details': errors,
                'preview_id': None,
            }, status=409)

        preview_id = uuid.uuid4().hex[:16]

        line_changes = _compute_line_changes(original_content, new_content)

        with _previews_lock:
            _diff_previews[preview_id] = {
                'session_id': body['session_id'],
                'path': body['path'],
                'blocks': blocks,
                'original': original_content,
                'new_content': new_content,
                'line_changes': line_changes,
                'source_agent': body.get('source_agent', 'unknown'),
                'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            }

        # Return snippets for inline preview (first 40 lines of old & new)
        orig_lines = original_content.split('\n')
        new_lines = new_content.split('\n')

        return j(handler, {
            'ok': True,
            'preview_id': preview_id,
            'path': body['path'],
            'blocks_count': len(blocks),
            'blocks': [{'start_line': b['start_line'], 'search_lines': len(b['search'].split('\n'))} for b in blocks],
            'original_snippet': '\n'.join(orig_lines[:60]),
            'new_snippet': '\n'.join(new_lines[:60]),
            'original_full': original_content,
            'new_full': new_content,
            'line_changes': line_changes,
            'source_agent': body.get('source_agent', None),
        })

    except (ValueError, PermissionError) as e:
        return bad(handler, str(e))


# ── POST /api/file/apply-preview ──────────────────────────────────────────────

def handle_post_file_apply_preview(handler, body) -> bool:
    """POST /api/file/apply-preview — apply a previously previewed diff.

    Body: { session_id, preview_id }
    Creates checkpoint before applying.
    Returns: { ok, path, blocks_applied, checkpoint_id }
    """
    try:
        require(body, 'session_id', 'preview_id')
    except ValueError as e:
        return bad(handler, str(e))

    preview_id = body['preview_id']

    with _previews_lock:
        preview = _diff_previews.get(preview_id)

    if not preview:
        return bad(handler, 'Preview not found (expired or invalid)', 404)

    if preview['session_id'] != body['session_id']:
        return bad(handler, 'Preview does not belong to this session', 403)

    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)

    try:
        target = safe_resolve(Path(s.workspace), preview['path'])
        if not target.exists():
            return bad(handler, 'Original file no longer exists', 404)

        original_content = target.read_text(encoding='utf-8')

        # Verify file hasn't changed since preview
        if original_content != preview['original']:
            return bad(handler, 'File has been modified since preview was created. Please re-preview.', 409)

        # Create checkpoint before modification
        cp_id = _create_checkpoint(body['session_id'], preview['path'], original_content)

        # Apply the previewed content
        target.write_text(preview['new_content'], encoding='utf-8')

        # Record change history
        _add_change_history(body['session_id'], {
            'time': time.strftime('%H:%M:%S'),
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'agent': preview.get('source_agent', 'unknown'),
            'file': preview['path'],
            'checkpoint_id': cp_id,
            'preview_id': preview_id,
            'line_changes': preview.get('line_changes', {}),
            'action': 'applied',
        })

        # Clean up preview
        with _previews_lock:
            _diff_previews.pop(preview_id, None)

        return j(handler, {
            'ok': True,
            'path': preview['path'],
            'blocks_applied': len(preview.get('blocks', [])),
            'checkpoint_id': cp_id,
            'applied': True,
        })

    except (ValueError, PermissionError) as e:
        return bad(handler, str(e))


# ── POST /api/file/reject-preview ─────────────────────────────────────────────

def handle_post_file_reject_preview(handler, body) -> bool:
    """POST /api/file/reject-preview — reject/discard a diff preview.

    Body: { session_id, preview_id }
    Returns: { ok, rejected: true }
    """
    try:
        require(body, 'session_id', 'preview_id')
    except ValueError as e:
        return bad(handler, str(e))

    preview_id = body['preview_id']

    with _previews_lock:
        preview = _diff_previews.get(preview_id)

    if not preview:
        return bad(handler, 'Preview not found (expired or invalid)', 404)

    if preview['session_id'] != body['session_id']:
        return bad(handler, 'Preview does not belong to this session', 403)

    # Record rejection in history
    _add_change_history(body['session_id'], {
        'time': time.strftime('%H:%M:%S'),
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'agent': preview.get('source_agent', 'unknown'),
        'file': preview['path'],
        'preview_id': preview_id,
        'line_changes': preview.get('line_changes', {}),
        'action': 'rejected',
    })

    with _previews_lock:
        _diff_previews.pop(preview_id, None)

    return j(handler, {
        'ok': True,
        'rejected': True,
        'preview_id': preview_id,
    })


# ── POST /api/file/apply-diff ─────────────────────────────────────────────────

def handle_post_file_apply_diff(handler, body) -> bool:
    """POST /api/file/apply-diff — apply search/replace diff blocks to a file.

    Body: { session_id, path, diff }
    A checkpoint is automatically created before modification.
    Returns: { ok, path, blocks_applied, checkpoint_id, applied }
    """
    try:
        require(body, 'session_id', 'path', 'diff')
    except ValueError as e:
        return bad(handler, str(e))

    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)

    try:
        target = safe_resolve(Path(s.workspace), body['path'])
        if not target.exists():
            return bad(handler, 'File not found', 404)
        if target.is_dir():
            return bad(handler, 'Cannot apply diff to a directory')

        original_content = target.read_text(encoding='utf-8')

        blocks = _parse_diff_blocks(body['diff'])

        if not blocks:
            return bad(handler, 'No valid diff blocks found. Use the SEARCH/REPLACE format.')

        # Auto-create checkpoint before modification
        cp_id = _create_checkpoint(body['session_id'], body['path'], original_content)

        new_content, errors = _apply_diff_blocks(original_content, blocks)

        if errors:
            return j(handler, {
                'ok': False,
                'error': 'Diff application failed',
                'details': errors,
                'checkpoint_id': cp_id,
                'applied': False,
            }, status=409)

        target.write_text(new_content, encoding='utf-8')

        # Record change history
        line_changes = _compute_line_changes(original_content, new_content)
        _add_change_history(body['session_id'], {
            'time': time.strftime('%H:%M:%S'),
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'agent': body.get('source_agent', 'direct'),
            'file': body['path'],
            'checkpoint_id': cp_id,
            'line_changes': line_changes,
            'action': 'applied',
        })

        return j(handler, {
            'ok': True,
            'path': body['path'],
            'blocks_applied': len(blocks),
            'checkpoint_id': cp_id,
            'applied': True,
        })

    except (ValueError, PermissionError) as e:
        return bad(handler, str(e))


# ── GET /api/diff/history ─────────────────────────────────────────────────────

def handle_get_diff_history(handler, parsed) -> bool:
    """GET /api/diff/history?session_id=... — list change history for a session."""
    from urllib.parse import parse_qs
    qs = parse_qs(parsed.query)
    sid = qs.get('session_id', [''])[0]
    if not sid:
        return bad(handler, 'session_id is required')

    with _history_lock:
        hist = list(_change_history.get(sid, []))

    return j(handler, {'history': hist, 'count': len(hist)})


# ── GET /api/diff/preview ─────────────────────────────────────────────────────

def handle_get_diff_preview(handler, parsed) -> bool:
    """GET /api/diff/preview?session_id=... — list active previews for a session."""
    from urllib.parse import parse_qs
    qs = parse_qs(parsed.query)
    sid = qs.get('session_id', [''])[0]
    if not sid:
        return bad(handler, 'session_id is required')

    with _previews_lock:
        previews = []
        for pid, prev in _diff_previews.items():
            if prev['session_id'] == sid:
                previews.append({
                    'preview_id': pid,
                    'path': prev['path'],
                    'line_changes': prev.get('line_changes', {}),
                    'source_agent': prev.get('source_agent'),
                    'created_at': prev.get('created_at'),
                })

    return j(handler, {'previews': previews, 'count': len(previews)})


# ── GET /api/checkpoints ──────────────────────────────────────────────────────

def handle_get_checkpoints(handler, parsed) -> bool:
    """GET /api/checkpoints?session_id=... — list checkpoints for a session."""
    from urllib.parse import parse_qs
    qs = parse_qs(parsed.query)
    sid = qs.get('session_id', [''])[0]
    if not sid:
        return bad(handler, 'session_id is required')

    cp_dir = _session_checkpoint_dir(sid)
    checkpoints = []
    if cp_dir.exists():
        for f in sorted(cp_dir.glob('*.json'), reverse=True):
            try:
                data = _json.loads(f.read_text(encoding='utf-8'))
                checkpoints.append(data)
            except Exception:
                pass

    return j(handler, {'checkpoints': checkpoints, 'count': len(checkpoints)})


# ── POST /api/checkpoints/rollback ────────────────────────────────────────────

def handle_post_checkpoint_rollback(handler, body) -> bool:
    """POST /api/checkpoints/rollback — restore file from a checkpoint.

    Body: { session_id, checkpoint_id }
    """
    try:
        require(body, 'session_id', 'checkpoint_id')
    except ValueError as e:
        return bad(handler, str(e))

    cp_dir = _session_checkpoint_dir(body['session_id'])
    cp_file = cp_dir / f"{body['checkpoint_id']}.json"

    if not cp_file.exists():
        return bad(handler, 'Checkpoint not found', 404)

    try:
        data = _json.loads(cp_file.read_text(encoding='utf-8'))
    except Exception:
        return bad(handler, 'Failed to read checkpoint')

    file_path = data.get('file_path', '')
    content = data.get('content', '')

    if not file_path:
        return bad(handler, 'Checkpoint has no file_path')

    try:
        target = Path(file_path)
        if target.is_absolute():
            # For absolute paths, verify it's inside a known workspace
            from api.workspace import load_workspaces as _load_workspaces
            wss = _load_workspaces()
            allowed = False
            for ws in wss:
                try:
                    target.relative_to(Path(ws['path']))
                    allowed = True
                    break
                except ValueError:
                    pass
            if not allowed:
                return bad(handler, 'Checkpoint file_path is outside known workspaces')
        else:
            # Relative path — resolve against session workspace
            try:
                s = get_session(body['session_id'])
            except KeyError:
                return bad(handler, 'Session not found', 404)
            target = Path(s.workspace) / file_path

        if not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)

        # Create a rollback checkpoint first (safety net)
        if target.exists():
            old_content = target.read_text(encoding='utf-8')
            _create_checkpoint(body['session_id'], str(target), old_content)

        target.write_text(content, encoding='utf-8')

        # Record rollback in history
        _add_change_history(body['session_id'], {
            'time': time.strftime('%H:%M:%S'),
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'agent': 'rollback',
            'file': str(target),
            'checkpoint_id': body['checkpoint_id'],
            'action': 'rollback',
        })

        return j(handler, {
            'ok': True,
            'rollback': True,
            'checkpoint_id': body['checkpoint_id'],
            'file_path': str(target),
        })

    except (ValueError, PermissionError) as e:
        return bad(handler, str(e))


# ── POST /api/checkpoints/delete ──────────────────────────────────────────────

def handle_post_checkpoint_delete(handler, body) -> bool:
    """POST /api/checkpoints/delete — delete a specific checkpoint or all for a session.

    Body: { session_id, checkpoint_id? }
    If checkpoint_id is provided, delete that one. Otherwise delete all for the session.
    """
    try:
        require(body, 'session_id')
    except ValueError as e:
        return bad(handler, str(e))

    cp_dir = _session_checkpoint_dir(body['session_id'])
    cpid = body.get('checkpoint_id', '').strip()

    if cpid:
        cp_file = cp_dir / f"{cpid}.json"
        if cp_file.exists():
            cp_file.unlink()
        return j(handler, {'ok': True, 'deleted': cpid})
    else:
        # Delete all checkpoints for this session
        count = 0
        if cp_dir.exists():
            for f in cp_dir.glob('*.json'):
                f.unlink()
                count += 1
        return j(handler, {'ok': True, 'deleted_count': count})
