import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.hashline_editor import (
    compute_line_hash,
    format_hashline,
    parse_anchor,
    hashline_read_tool,
    hashline_edit_tool,
    StaleEditError,
)
from tools.file_tools import patch_tool


class TestHashlineEditor(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.test_file = Path(self.tmpdir.name) / "sample.py"
        self.initial_content = (
            "import os\n"
            "import sys\n"
            "\n"
            "def calculate(a, b):\n"
            "    result = a + b\n"
            "    return result\n"
            "\n"
            "def main():\n"
            "    print(calculate(10, 20))\n"
        )
        self.test_file.write_text(self.initial_content, encoding="utf-8")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_compute_line_hash_consistency(self):
        # Hash should be identical regardless of trailing \n or \r\n
        h1 = compute_line_hash("import os\n")
        h2 = compute_line_hash("import os\r\n")
        h3 = compute_line_hash("import os")
        self.assertEqual(h1, h2)
        self.assertEqual(h1, h3)
        self.assertEqual(len(h1), 2)

    def test_hashline_read(self):
        res_raw = hashline_read_tool(str(self.test_file), offset=1, limit=3)
        res = json.loads(res_raw)
        self.assertIn("content", res)
        lines = res["content"].splitlines()
        self.assertEqual(len(lines), 3)

        # First line should be line 1 with its checksum
        h1 = compute_line_hash("import os")
        expected_line1 = f"1#{h1}| import os"
        self.assertEqual(lines[0], expected_line1)

    def test_set_line_success(self):
        h1 = compute_line_hash("import os")
        ops = [
            {"op": "set_line", "anchor": f"1#{h1}", "content": "import os, sys, time"}
        ]
        res_raw = hashline_edit_tool(str(self.test_file), operations=ops)
        res = json.loads(res_raw)
        self.assertTrue(res.get("success"), res.get("error"))

        # Verify file content
        updated = self.test_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(updated[0], "import os, sys, time")

    def test_replace_lines_success(self):
        # Replace calculate body (lines 4 to 6)
        h4 = compute_line_hash("def calculate(a, b):")
        h6 = compute_line_hash("    return result")
        ops = [
            {
                "op": "replace_lines",
                "start": f"4#{h4}",
                "end": f"6#{h6}",
                "content": "def calculate(a, b):\n    return a * b",
            }
        ]
        res_raw = hashline_edit_tool(str(self.test_file), operations=ops)
        res = json.loads(res_raw)
        self.assertTrue(res.get("success"), res.get("error"))

        updated = self.test_file.read_text(encoding="utf-8")
        self.assertIn("return a * b", updated)
        self.assertNotIn("result = a + b", updated)

    def test_stale_edit_protection(self):
        # Intentionally provide a wrong checksum #ZZ
        ops = [
            {"op": "set_line", "anchor": "1#ZZ", "content": "corrupted line"}
        ]
        res_raw = hashline_edit_tool(str(self.test_file), operations=ops)
        res = json.loads(res_raw)
        self.assertIn("error", res)
        self.assertIn("STALE_EDIT_ERROR", res["error"])

        # File must remain untouched!
        current = self.test_file.read_text(encoding="utf-8")
        self.assertEqual(current, self.initial_content)

    def test_dsl_text_parsing_and_execution(self):
        h8 = compute_line_hash("def main():")
        dsl = f"""
        INSERT_AFTER 8#{h8}
            # log execution
        END_INSERT
        """
        res_raw = hashline_edit_tool(str(self.test_file), operations=dsl)
        res = json.loads(res_raw)
        self.assertTrue(res.get("success"), res.get("error"))

        updated = self.test_file.read_text(encoding="utf-8")
        self.assertIn("# log execution", updated)

    def test_patch_tool_hashline_integration(self):
        h1 = compute_line_hash("import os")
        patch_payload = f"SET 1#{h1}\nimport pathlib\nEND_SET"
        res_raw = patch_tool(mode="hashline", path=str(self.test_file), patch=patch_payload)
        res = json.loads(res_raw)
        self.assertTrue(res.get("success"), res.get("error"))

        updated = self.test_file.read_text(encoding="utf-8")
        self.assertTrue(updated.startswith("import pathlib\n"))


if __name__ == "__main__":
    unittest.main()
