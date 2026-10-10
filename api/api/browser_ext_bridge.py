import threading
import time
import uuid
import logging
from collections import deque

_lock = threading.Lock()
_commands = deque()
_results = {}
_last_poll = None
_MAX_QUEUE = 200


def enqueue(command: dict) -> str:
    cmd_id = uuid.uuid4().hex
    item = dict(command)
    item['id'] = cmd_id
    with _lock:
        if len(_commands) >= _MAX_QUEUE:
            dropped = _commands.popleft()
            logging.getLogger(__name__).warning('browser extension command queue full; dropped %s', dropped.get('id'))
        _commands.append(item)
    return cmd_id


def wait_result(cmd_id: str, timeout: float) -> dict | None:
    deadline = time.monotonic() + max(0, timeout)
    while True:
        with _lock:
            result = _results.pop(cmd_id, None)
        if result is not None:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(0.25, remaining))


def poll_command() -> dict | None:
    global _last_poll
    with _lock:
        _last_poll = time.monotonic()
        return _commands.popleft() if _commands else None


def put_result(cmd_id: str, result: dict) -> None:
    with _lock:
        _results[cmd_id] = result


def status() -> dict:
    with _lock:
        ago = None if _last_poll is None else max(0.0, time.monotonic() - _last_poll)
        return {'ok': True, 'connected': ago is not None and ago <= 5.0,
                'last_poll_ago': ago, 'queue_len': len(_commands)}
