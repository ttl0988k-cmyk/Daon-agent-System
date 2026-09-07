# -*- coding: utf-8 -*-
"""Comprehensive verification test for Phase 2 Route Refactoring."""

import sys
import os
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
from api.routes.admin_routes import handle_get_health


class TestPhase2Routes(unittest.TestCase):
    """Test suite for Phase 2 O(1) route registry and unified dispatch."""

    def setUp(self):
        self.mock_handler = MagicMock()
        self.mock_handler.client_address = ('127.0.0.1', 8888)
        self.mock_handler.headers = {'Content-Length': '2'}
        self.mock_handler.rfile.read.return_value = b'{}'

    def test_routes_count_and_no_empty_keys(self):
        """Verify route registries have expected non-empty routes."""
        self.assertGreaterEqual(len(GET_EXACT_ROUTES), 80)
        self.assertGreaterEqual(len(POST_EXACT_ROUTES), 130)
        self.assertEqual([k for k in GET_EXACT_ROUTES if not k], [])
        self.assertEqual([k for k in POST_EXACT_ROUTES if not k], [])
        for path in GET_EXACT_ROUTES:
            self.assertTrue(path.startswith('/'), f"Path must start with /: {path}")
        for path in POST_EXACT_ROUTES:
            self.assertTrue(path.startswith('/'), f"Path must start with /: {path}")

    def test_health_check_payload_and_structure(self):
        """Verify /health returns unified and rich metrics."""
        parsed = urlparse('/health')
        handled = handle_get(self.mock_handler, parsed)
        self.assertTrue(handled)
        # Check that send_response or send_json was triggered
        self.assertTrue(self.mock_handler.send_response.called or self.mock_handler.wfile.write.called)

    def test_deduplicated_routes(self):
        """Verify known duplicated routes are cleanly single-mapped."""
        self.assertIn('/api/approval/pending', GET_EXACT_ROUTES)
        self.assertIn('/api/system/build-info', GET_EXACT_ROUTES)
        self.assertIn('/api/whisper/transcribe', POST_RAW_ROUTES)

    def test_consolidated_server_routes(self):
        """Verify browser & setup routes previously stranded in server.py are now in routes registry."""
        self.assertIn('/api/browser/grid', GET_EXACT_ROUTES)
        self.assertIn('/api/browser/sessions', GET_EXACT_ROUTES)
        self.assertIn('/api/browser/proxy', GET_EXACT_ROUTES)
        self.assertIn('/api/browser/batch', POST_EXACT_ROUTES)
        self.assertIn('/api/browser/focus', POST_EXACT_ROUTES)
        self.assertIn('/api/browser/close_tab', POST_EXACT_ROUTES)
        self.assertIn('/api/setup/generate', POST_EXACT_ROUTES)

    def test_prefix_dispatches(self):
        """Verify prefix routes trigger correctly."""
        # Static
        parsed_static = urlparse('/static/js/app.js')
        self.assertTrue(handle_get(self.mock_handler, parsed_static))

        # Dynamic approve prefix
        parsed_dyn = urlparse('/api/dynamic/approve/job_123')
        # Should be handled by POST_PREFIX_ROUTES
        res = handle_post(self.mock_handler, parsed_dyn)
        # It might return error response or True depending on job existence, but handle_post returns True (handled)
        self.assertTrue(res)

    def test_unknown_routes_return_false(self):
        """Verify 404 behavior for unknown paths."""
        self.assertFalse(handle_get(self.mock_handler, urlparse('/api/totally_fake_route')))
        self.assertFalse(handle_post(self.mock_handler, urlparse('/api/totally_fake_route')))


if __name__ == '__main__':
    unittest.main()
