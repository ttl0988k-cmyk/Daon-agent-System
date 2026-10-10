from api.helpers import j_ok, j_err
from api import browser_ext_bridge


def handle_get_browser_ext(handler, parsed) -> bool:
    if parsed.path == '/api/browser-ext/poll':
        return j_ok(handler, {'ok': True, 'command': browser_ext_bridge.poll_command()})
    if parsed.path == '/api/browser-ext/health':
        return j_ok(handler, browser_ext_bridge.status())
    return False


def handle_post_browser_ext(handler, body) -> bool:
    path = handler.path.split('?', 1)[0]
    if path == '/api/browser-ext/enqueue':
        if 'command' not in body:
            return j_err(handler, 'command is required', status=400)
        return j_ok(handler, {'ok': True, 'id': browser_ext_bridge.enqueue(body['command'])})
    if path == '/api/browser-ext/result':
        if 'id' not in body:
            return j_err(handler, 'id is required', status=400)
        browser_ext_bridge.put_result(body['id'], body.get('result'))
        return j_ok(handler, {'ok': True})
    return False
