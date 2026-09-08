# -*- coding: utf-8 -*-
"""Comprehensive verification test for Phase 2 Route Refactoring.

Tests exact O(1) dictionary routing, path-boundary-delimited prefix matching,
and deep JSON schema validation on response payloads.
"""

import io
import json
import os
import re
import sys
import unittest
from unittest.mock import MagicMock
from urllib.parse import urlparse

# Ensure api is importable
sys.path.insert(0, os.path.abspath('api'))

from api.routes import (
    handle_get,
    handle_post,
    GET_EXACT_ROUTES,
    GET_PREFIX_ROUTES,
    POST_RAW_ROUTES,
    POST_PARSED_ROUTES,
    POST_EXACT_ROUTES,
    POST_PREFIX_ROUTES,
)


class FakeHandler:
    """Realistic HTTP request handler mock capturing responses and payloads."""

    def __init__(self, body_bytes=b'{}', headers=None):
        self.client_address = ('127.0.0.1', 8888)
        self.headers = {'Content-Length': str(len(body_bytes))}
        if headers:
            self.headers.update(headers)
        self.rfile = io.BytesIO(body_bytes)
        self.wfile = io.BytesIO()
        self.status = 200
        self.response_headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers[key] = value

    def end_headers(self):
        pass

    def send_json(self, data, status=200):
        self.status = status
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.wfile.write(payload)
        return True

    def send_error_json(self, message, status=400):
        self.status = status
        payload = json.dumps({'ok': False, 'error': message}, ensure_ascii=False).encode('utf-8')
        self.wfile.write(payload)
        return True

    def get_json(self):
        val = self.wfile.getvalue()
        if not val:
            return None
        return json.loads(val.decode('utf-8'))


class TestPhase2Routes(unittest.TestCase):
    """Test suite for Phase 2 exact O(1) route registry, prefix boundaries, and payload schemas."""

    def setUp(self):
        self.handler = FakeHandler()

    def test_routes_count_and_naming_conventions(self):
        """Verify route registries have expected counts and strict path conventions."""
        self.assertGreaterEqual(len(GET_EXACT_ROUTES), 85)
        self.assertGreaterEqual(len(POST_EXACT_ROUTES), 130)

        # Exact routes must start with '/' and not end with '/' unless root '/'
        for path in GET_EXACT_ROUTES:
            self.assertTrue(path.startswith('/'), f"Path must start with /: {path}")
            if len(path) > 1:
                self.assertFalse(path.endswith('/'), f"Exact path must not end with /: {path}")

        for path in POST_EXACT_ROUTES:
            self.assertTrue(path.startswith('/'), f"Path must start with /: {path}")
            if len(path) > 1:
                self.assertFalse(path.endswith('/'), f"Exact path must not end with /: {path}")

        # All prefix routes MUST end with '/' to guarantee directory boundary matching
        for prefix, _ in GET_PREFIX_ROUTES:
            self.assertTrue(prefix.endswith('/'), f"GET prefix route must end with / for boundary safety: {prefix}")

        for prefix, _ in POST_PREFIX_ROUTES:
            self.assertTrue(prefix.endswith('/'), f"POST prefix route must end with / for boundary safety: {prefix}")

    def test_health_check_payload_and_schema_validation(self):
        """Verify /health returns HTTP 200 with complete, validated schema."""
        parsed = urlparse('/health')
        handled = handle_get(self.handler, parsed)
        self.assertTrue(handled, "/health was not handled")
        self.assertEqual(self.handler.status, 200)

        payload = self.handler.get_json()
        self.assertIsNotNone(payload, "Payload must not be empty")

        # Deep schema assertions
        self.assertEqual(payload.get('status'), 'ok')
        self.assertIsInstance(payload.get('healthy'), bool)
        self.assertIsInstance(payload.get('uptime_seconds'), int)
        self.assertGreaterEqual(payload.get('uptime_seconds'), 0)
        self.assertRegex(payload.get('uptime_display', ''), r'^\d{2,}:\d{2}:\d{2}$')
        self.assertIsInstance(payload.get('thread_count'), int)
        self.assertGreater(payload.get('thread_count'), 0)
        self.assertIsInstance(payload.get('active_streams'), int)
        self.assertIsInstance(payload.get('active_agents'), int)
        self.assertIn(payload.get('session_store'), ('ok', 'dir_missing'))
        self.assertIn(payload.get('skill_registry'), ('ok', 'dir_missing'))
        self.assertEqual(payload.get('pid'), os.getpid())

    def test_prefix_boundary_strictness(self):
        """Verify exact routes are matched and non-existent prefix extensions return False."""
        # /api/skills/search is exact route
        self.assertIn('/api/skills/search', GET_EXACT_ROUTES)
        # Should be handled when queried with search terms
        h1 = FakeHandler()
        res1 = handle_get(h1, urlparse('/api/skills/search?q=pdf'))
        self.assertTrue(res1)

        # /api/skills/searchfoo is NOT a valid route and must return False (404)
        h2 = FakeHandler()
        res2 = handle_get(h2, urlparse('/api/skills/searchfoo'))
        self.assertFalse(res2, "/api/skills/searchfoo should NOT match /api/skills/search")

        # /api/skills/recommend is exact route
        self.assertIn('/api/skills/recommend', GET_EXACT_ROUTES)
        h3 = FakeHandler()
        res3 = handle_get(h3, urlparse('/api/skills/recommendfoo'))
        self.assertFalse(res3, "/api/skills/recommendfoo should NOT match /api/skills/recommend")

    def test_migrated_server_endpoints_and_schemas(self):
        """Verify all routes previously stranded in server.py are now in routes with correct schemas."""
        # 1. /api/score/evaluate
        self.assertIn('/api/score/evaluate', GET_EXACT_ROUTES)
        h_score = FakeHandler()
        self.assertTrue(handle_get(h_score, urlparse('/api/score/evaluate')))
        data = h_score.get_json()
        self.assertTrue(data.get('ok'), "Score evaluate should return ok=True")
        self.assertIn('total_score', data)
        self.assertIsInstance(data['total_score'], (int, float))
        self.assertIn('max_score', data)
        self.assertIn('grade', data)
        self.assertIn('categories', data)
        self.assertIsInstance(data['categories'], list)


        # 2. /api/fs/list root drives
        self.assertIn('/api/fs/list', GET_EXACT_ROUTES)
        h_fs = FakeHandler()
        self.assertTrue(handle_get(h_fs, urlparse('/api/fs/list')))
        fs_data = h_fs.get_json()
        self.assertIn('drives', fs_data)
        self.assertIn('entries', fs_data)
        self.assertIsInstance(fs_data['entries'], list)

        # 3. /api/setup/preview missing workspace returns 400
        self.assertIn('/api/setup/preview', GET_EXACT_ROUTES)
        h_setup = FakeHandler()
        self.assertTrue(handle_get(h_setup, urlparse('/api/setup/preview')))
        self.assertEqual(h_setup.status, 400)
        self.assertFalse(h_setup.get_json().get('ok'))

        # 4. /api/setup/detect missing workspace returns 400
        self.assertIn('/api/setup/detect', GET_EXACT_ROUTES)
        h_detect = FakeHandler()
        self.assertTrue(handle_get(h_detect, urlparse('/api/setup/detect')))
        self.assertEqual(h_detect.status, 400)

        # 5. /api/mcp/recommend missing workspace returns 400
        self.assertIn('/api/mcp/recommend', GET_EXACT_ROUTES)
        h_mcp = FakeHandler()
        self.assertTrue(handle_get(h_mcp, urlparse('/api/mcp/recommend')))
        self.assertEqual(h_mcp.status, 400)

        # 6. /api/dynamic/status missing run_id returns 400
        self.assertIn('/api/dynamic/status', GET_EXACT_ROUTES)
        h_dyn = FakeHandler()
        self.assertTrue(handle_get(h_dyn, urlparse('/api/dynamic/status')))
        self.assertEqual(h_dyn.status, 400)
        self.assertIn('run_id is required', h_dyn.get_json().get('error', ''))

        # 7. /api/speak/tts missing text returns 400
        self.assertIn('/api/speak/tts', GET_EXACT_ROUTES)
        h_tts = FakeHandler()
        self.assertTrue(handle_get(h_tts, urlparse('/api/speak/tts')))
        self.assertEqual(h_tts.status, 400)

        # 8. /api/dynamic/run in POST_EXACT_ROUTES
        self.assertIn('/api/dynamic/run', POST_EXACT_ROUTES)
        h_dyn_run = FakeHandler(body_bytes=b'{}')
        self.assertTrue(handle_post(h_dyn_run, urlparse('/api/dynamic/run')))
        self.assertEqual(h_dyn_run.status, 400)  # Missing task

        # 9. /api/dynamic/cancel in POST_EXACT_ROUTES
        self.assertIn('/api/dynamic/cancel', POST_EXACT_ROUTES)
        h_dyn_cancel = FakeHandler(body_bytes=b'{}')
        self.assertTrue(handle_post(h_dyn_cancel, urlparse('/api/dynamic/cancel')))
        self.assertEqual(h_dyn_cancel.status, 400)  # Missing run_id

    def test_upload_route_in_post_raw_routes(self):
        """Verify /api/upload is in POST_RAW_ROUTES and handles raw multipart without read_body interference."""
        self.assertIn('/api/upload', POST_RAW_ROUTES)
        self.assertNotIn('/api/upload', POST_EXACT_ROUTES)

        # Build multipart payload
        boundary = '----WebKitFormBoundaryTest12345'
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="session_id"\r\n\r\n'
            f'non_existent_session_id\r\n'
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="test.png"\r\n'
            f'Content-Type: image/png\r\n\r\n'
            f'fake_image_bytes\r\n'
            f'--{boundary}--\r\n'
        ).encode('utf-8')

        headers = {
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Content-Length': str(len(body)),
        }
        h_upload = FakeHandler(body_bytes=body, headers=headers)
        # handle_post should call handle_post_upload directly from POST_RAW_ROUTES
        result = handle_post(h_upload, urlparse('/api/upload'))
        self.assertTrue(result)
        # Because non_existent_session_id doesn't exist, it should return 404 (Session not found), NOT 400 or hang!
        self.assertEqual(h_upload.status, 404)
        self.assertEqual(h_upload.get_json().get('error'), 'Session not found')

    def test_native_dialog_routes(self):
        """Verify native dialog routes exist in exact registry."""
        self.assertIn('/api/workspaces/select', GET_EXACT_ROUTES)
        self.assertIn('/api/file/select', GET_EXACT_ROUTES)

    def test_unknown_routes_return_false(self):
        """Verify 404 behavior for unknown paths."""
        self.assertFalse(handle_get(self.handler, urlparse('/api/totally_fake_route')))
        self.assertFalse(handle_post(self.handler, urlparse('/api/totally_fake_route')))


if __name__ == '__main__':
    unittest.main()

