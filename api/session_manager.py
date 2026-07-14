"""
DEPRECATED — Use api.models instead.

This module is kept for backward compatibility during migration.
All session operations should use api.models.Session and related functions.
"""
import json
import time
import uuid
import warnings
from pathlib import Path
from api.config import SESSION_DIR, LOCK

warnings.warn(
    "api.session_manager is deprecated. Use api.models instead.",
    DeprecationWarning,
    stacklevel=2,
)

class Session:
    def __init__(self, session_id=None, title='Untitled', workspace='', model='minimax-m3', messages=None, tool_calls=None, pinned=False, archived=False, created_at=None, updated_at=None):
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.title = title
        self.workspace = workspace
        self.model = model
        self.messages = messages or []
        self.tool_calls = tool_calls or []
        self.pinned = pinned
        self.archived = archived
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or time.time()

    def save(self):
        p = SESSION_DIR / f"{self.session_id}.json"
        data = {
            'session_id': self.session_id,
            'title': self.title,
            'workspace': self.workspace,
            'model': self.model,
            'messages': self.messages,
            'tool_calls': self.tool_calls,
            'pinned': self.pinned,
            'archived': self.archived,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }
        with LOCK:
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')

    def compact(self):
        return {
            'session_id': self.session_id,
            'title': self.title,
            'workspace': self.workspace,
            'model': self.model,
            'pinned': self.pinned,
            'archived': self.archived,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'message_count': len(self.messages)
        }

def get_session(session_id) -> Session:
    p = SESSION_DIR / f"{session_id}.json"
    if not p.exists():
        raise KeyError(f"Session {session_id} not found")
    with LOCK:
        data = json.loads(p.read_text(encoding='utf-8'))
    return Session(
        session_id=data.get('session_id'),
        title=data.get('title', 'Untitled'),
        workspace=data.get('workspace', ''),
        model=data.get('model', 'minimax-m3'),
        messages=data.get('messages', []),
        tool_calls=data.get('tool_calls', []),
        pinned=data.get('pinned', False),
        archived=data.get('archived', False),
        created_at=data.get('created_at'),
        updated_at=data.get('updated_at')
    )

def new_session(workspace='', model='minimax-m3') -> Session:
    s = Session(workspace=workspace, model=model)
    s.save()
    return s

def all_sessions():
    sessions = []
    with LOCK:
        for p in SESSION_DIR.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding='utf-8'))
                s = Session(
                    session_id=data.get('session_id'),
                    title=data.get('title', 'Untitled'),
                    workspace=data.get('workspace', ''),
                    model=data.get('model', 'minimax-m3'),
                    pinned=data.get('pinned', False),
                    archived=data.get('archived', False),
                    created_at=data.get('created_at'),
                    updated_at=data.get('updated_at')
                )
                sessions.append(s.compact())
            except Exception:
                pass
    sessions.sort(key=lambda x: x['updated_at'], reverse=True)
    return sessions

def delete_session(session_id):
    p = SESSION_DIR / f"{session_id}.json"
    with LOCK:
        if p.exists():
            p.unlink()
