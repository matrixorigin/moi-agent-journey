import json
import tempfile
import unittest
from pathlib import Path

from journey.ui_contract import (A2UI, MCP_APP, MIXED_PATH, UNSUPPORTED_PATH,
                                 detect_ui_path, path_allowed, validate_ui_contract)


class UIContractTests(unittest.TestCase):
    def write(self, events):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "events.jsonl"
        path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        return path

    def test_a2ui_mcp_mixed_and_unsupported_detection(self):
        a2ui = {"task_id": "task", "kind": "a2ui", "payload": {"version": "v0.9"}}
        mcp = {"task_id": "task", "kind": "tool_result", "payload": {
            "tool_id": "present_mcp_app", "success": True,
            "mcp_app_resource_uri": "ui://moi-generated/test"}}
        self.assertEqual(detect_ui_path(self.write([a2ui]), {"task"}), A2UI)
        self.assertEqual(detect_ui_path(self.write([mcp]), {"task"}), MCP_APP)
        self.assertEqual(detect_ui_path(self.write([a2ui, mcp]), {"task"}), MIXED_PATH)
        self.assertEqual(detect_ui_path(self.write([]), {"task"}), UNSUPPORTED_PATH)

    def test_contract_is_additive_and_validated(self):
        case = {"ui_contract": {"allowed_paths": ["a2ui", "mcp_app"],
                                "selection": "first_valid_evidence", "mixed_path": "fail"}}
        self.assertTrue(path_allowed(case, A2UI))
        self.assertTrue(path_allowed(case, MCP_APP))
        self.assertFalse(path_allowed(case, MIXED_PATH))
        self.assertFalse(path_allowed({}, A2UI))
        with self.assertRaisesRegex(ValueError, "invalid_ui_contract"):
            validate_ui_contract({"ui_contract": {"allowed_paths": ["other"]}})


if __name__ == "__main__":
    unittest.main()
