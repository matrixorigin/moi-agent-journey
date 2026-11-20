import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from journey.evidence import Evidence
from journey.catalog import load_case
from journey.mcp_app_checks import evaluate_mcp_app, expected_mcp_session_check_count, self_contained_errors


HTML = """<!doctype html><html><body>
<canvas id="chart"></canvas><div>North South Sales 12 7</div>
<table><tr><th>item</th><th>count</th></tr><tr><td>North</td><td>12</td></tr><tr><td>South</td><td>7</td></tr></table>
<form><div id="question">2+3 等于多少？</div>
<button class="option" id="two">2</button><button class="option" id="three">3</button>
<button class="option" id="four">4</button><button class="option" id="five">5</button>
<input id="answer" type="text"><button id="submit">提交</button><div id="result"></div></form>
<input id="project" type="text"><select id="kind"><option value="exact">exact</option><option value="all">all</option></select>
<script>
document.querySelectorAll('.option').forEach(option => {
  option.onclick = () => {
    document.getElementById('result').textContent = '答对了';
  };
});
document.querySelector('form').onsubmit = event => {
  event.preventDefault();
  document.getElementById('result').textContent = '答对了';
  document.querySelectorAll('button').forEach(button => { button.disabled = true; });
};
</script></body></html>"""


class FakeClient:
    def __init__(self, html):
        self.html = html

    def product(self, method, path, body=None):
        return {"uri": body["uri"], "contents": [{"uri": body["uri"],
                "mimeType": "text/html;profile=mcp-app", "text": self.html}]}


class MCPAppChecksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.execution = SimpleNamespace(client=FakeClient(HTML), root="/workspaces/workspace",
                                         evidence=Evidence(self.root))
        self.task = {"task_id": "task"}

    def tearDown(self):
        self.temp.cleanup()

    def events(self, count=1):
        events = [{"task_id": "task", "kind": "tool_result", "payload": {
            "tool_id": "present_mcp_app", "success": True,
            "mcp_app_resource_uri": "ui://moi-generated/" + str(index)}} for index in range(count)]
        (self.root / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))

    def evaluate(self, expected, submission=None):
        self.events()
        turn = expected if "a2ui_evidence" in expected else {"a2ui_evidence": expected}
        return evaluate_mcp_app(self.execution, self.task, "surface", turn, {"code": "J2_FORM_94DEEPSEEK"}, submission)

    def test_chart_table_and_self_contained_checks(self):
        checks = self.evaluate({"component": "Chart", "fields": {"type": "bar"}})
        self.assertTrue(all(check["passed"] for check in checks), checks)
        checks = self.evaluate({"component": "Table", "fields": {"rows": [
            {"item": "North", "count": 12}, {"item": "South", "count": 7}]}})
        self.assertTrue(all(check["passed"] for check in checks), checks)

    def test_question_single_choice_interaction(self):
        expected = {"component": "Question", "fields": {"type": "single"}}
        submission = {"a2ui_submit": {"labels": ["4", "5"], "answer": "5"}}
        checks = self.evaluate(expected, submission)
        self.assertTrue(all(check["passed"] for check in checks), checks)

    def test_multiple_and_text_interaction(self):
        multiple = self.evaluate({"component": "Question", "fields": {"type": "multiple"}},
                                 {"a2ui_submit": {"type": "multiple", "labels": ["2", "3", "4"], "answer": ["2", "4"]}})
        self.assertTrue(all(check["passed"] for check in multiple), multiple)
        text = self.evaluate({"component": "Question", "fields": {"type": "text"}},
                             {"a2ui_submit": {"type": "text", "labels": [], "answer": "cats"}})
        self.assertTrue(all(check["passed"] for check in text), text)

    def test_form_submission(self):
        expected = {"component": "Form", "fields": {}}
        submission = {"a2ui_form_submit": {"fields": {"项目": "{code}", "类型": ["exact"]}}}
        checks = self.evaluate(expected, submission)
        self.assertTrue(all(check["passed"] for check in checks), checks)

    def test_form_radio_options_are_clicked_and_verified(self):
        self.execution.client.html = """<!doctype html><html><body><form>
            <input id="project" type="text">
            <input type="radio" name="kind" value="exact"><input type="radio" name="kind" value="all">
            <button type="submit">提交</button></form></body></html>"""
        checks = self.evaluate({"component": "Form", "fields": {}},
                               {"a2ui_form_submit": {"fields": {"项目": "{code}", "类型": ["exact"]}}})
        self.assertTrue(all(check["passed"] for check in checks), checks)

    def test_external_dependencies_and_duplicate_resources_fail(self):
        self.assertTrue(self_contained_errors("<html><script src='https://example.invalid/a.js'></script></html>"))
        self.events(2)
        checks = evaluate_mcp_app(self.execution, self.task, "surface", {"component": "Chart", "fields": {"type": "bar"}}, {})
        self.assertFalse(checks[-1]["passed"])
        self.assertEqual(checks[-1]["detail"], "duplicate_mcp_app_resources:2")

    def test_expected_counts_include_form_reply_but_not_local_question_turn(self):
        for case_id, expected in (("J2-61", 6), ("J2-62", 6), ("J2-69", 9),
                                  ("J2-74", 9), ("J2-75", 9), ("J2-94", 12)):
            with self.subTest(case_id=case_id):
                case = load_case(case_id)
                self.assertEqual(expected_mcp_session_check_count(case["sessions"][0]), expected)


if __name__ == "__main__":
    unittest.main()
