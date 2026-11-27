import json
import tempfile
import unittest
from pathlib import Path

from journey.j2_scoring.score import score_functional_result


def definition(expected=2):
    return {"id": "J2-X", "sessions": [{"id": "one", "expected_checks": expected, "turns": []}]}


def result(status="passed", checks=None, **updates):
    value = {"case_id": "J2-X", "title": "offline", "workspace": "journey-2", "run_id": "run-1",
             "status": status, "tasks": [{"task_id": "task-1"}], "conversations": [],
             "failures": [], "harness_errors": [], "validation": {"checks": checks or []}}
    value.update(updates)
    return value


def check(passed=True, rule="rule"):
    return {"session": "one", "rule": rule, "passed": passed,
            "detail": "" if passed else "mismatch"}


class ScoreOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.evidence = self.root / "result.json"

    def tearDown(self):
        self.temp.cleanup()

    def score(self, value, case=None):
        self.evidence.write_text(json.dumps(value), encoding="utf-8")
        return score_functional_result(value, self.evidence, case or definition(), self.root / "case.json")

    def test_pass_partial_and_fail_depend_only_on_score(self):
        passed = self.score(result(checks=[check(), check()]))
        partial = self.score(result(status="failed", checks=[check(), check(False)]))
        failed = self.score(result(status="failed", checks=[check(False), check(False)]))
        self.assertEqual((passed["score"], passed["verdict"]), ("100.00", "PASS"))
        self.assertEqual((partial["score"], partial["verdict"]), ("50.00", "PARTIAL"))
        self.assertEqual((failed["score"], failed["verdict"]), ("0.00", "FAIL"))
        for case in (passed, partial, failed):
            self.assertNotIn("gate_status", case)
            self.assertTrue(all("hard_gate" not in item for item in case["checkpoints"]))

    def test_third_rounding_and_equal_weights(self):
        scored = self.score(result(status="failed", checks=[check(), check(False), check(False)]), definition(3))
        self.assertEqual(scored["score"], "33.33")
        self.assertEqual(scored["verdict"], "PARTIAL")
        self.assertEqual([item["weight"] for item in scored["checkpoints"]], ["1", "1", "1"])

    def test_timeout_keeps_timeout_instead_of_framework_error_or_zero(self):
        scored = self.score(result(status="timeout", checks=[]))
        self.assertIsNone(scored["score"])
        self.assertIsNone(scored["verdict"])
        self.assertEqual(scored["reason"], "timeout")

    def test_framework_error_never_produces_score(self):
        value = result(checks=[check(), check()])
        value["harness_errors"] = [{"stage": "cleanup", "reason": "http_error"}]
        scored = self.score(value)
        self.assertIsNone(scored["score"])
        self.assertEqual(scored["reason"], "framework_error")

    def test_missing_checkpoints_and_empty_checks_remain_distinct(self):
        missing = self.score(result(status="failed", checks=[check(False)]))
        empty = self.score(result(status="failed", checks=[]))
        self.assertEqual(missing["reason"], "checkpoints_missing")
        self.assertEqual(empty["reason"], "empty_checks")
        self.assertIsNone(missing["score"])
        self.assertIsNone(empty["score"])

    def test_blocked_and_version_change_are_not_scored(self):
        blocked = self.score(result(status="blocked", checks=[check(), check()]))
        changed = self.score(result(checks=[check(), check()], failures=[
            {"stage": "qa_version", "reason": "qa_backend_changed_during_case"}]))
        self.assertEqual(blocked["reason"], "blocked")
        self.assertEqual(changed["reason"], "blocked")
        self.assertIsNone(changed["score"])

    def test_invalid_checks_and_not_run_are_unscored(self):
        invalid = self.score(result(checks=[check(), {"rule": "missing boolean"}]))
        not_run = self.score(result(status="not_run", checks=[check(), check()]))
        self.assertEqual(invalid["reason"], "invalid_checks")
        self.assertEqual(not_run["reason"], "not_run")
