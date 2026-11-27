import unittest

from journey.j2_scoring.compare import display, markdown
from journey.j2_scoring.html import render_html


def sample_case():
    side = {
        "case_id": "J2-02", "domain": "multimodal", "title": "识别图片",
        "execution_status": "passed", "score": "100.00", "verdict": "PASS",
        "excluded_reason": None, "normal_completed": True, "expected_checks": 2,
        "actual_checks": 2, "passed_checks": 2, "forced_after_timeout": False,
        "assertions_complete": True, "qa_version_start": {"version": "v1", "git_commit": "abc"},
        "qa_version_end": {"version": "v1", "git_commit": "abc"}, "harness_errors": [],
        "evidence_path": "/evidence/J2-02", "task_seconds": 30.0, "attempts": [],
    }
    return {
        "case_id": "J2-02", "domain": "multimodal", "title": "识别图片",
        "same_case_definition": True, "baseline_case_sha256": "case", "current_case_sha256": "case",
        "comparison_status": "comparable", "score_delta_current_minus_baseline": "0.00",
        "task_seconds_delta_current_minus_baseline": 0.0,
        "baseline": side, "current": {**side, "task_seconds": 40.0},
    }


def sample_report():
    summary = {
        "versions": ["v1"], "scored": 1, "total": 1, "coverage": "100.00",
        "normal_completed": 1, "normal_completion_coverage": "100.00", "passed": 1,
        "partial": 0, "failed": 0, "timeouts": 0, "framework_errors": 0, "blocked": 0,
    }
    return {
        "schema_version": "moi-production-run-comparison-v1", "suite_id": "sample", "model": "model",
        "baseline": {"run": "baseline", **summary},
        "current": {"run": "current", **summary, "max_workers": 2},
        "comparability": {"suite_sha256": "suite", "case_definition_changed": [],
                          "version_mismatch": [], "score_unavailable": [],
                          "fully_comparable_scored_cases": 1, "current_version_changed_during_run": False},
        "status_transitions": [{"baseline": "PASS", "current": "PASS", "count": 1}],
        "duration_thresholds": {
            "baseline": {"total_cases": 1, "numeric_task_seconds": 1,
                         "within_100_seconds": 1, "within_200_seconds": 1},
            "current": {"total_cases": 1, "numeric_task_seconds": 1,
                        "within_100_seconds": 1, "within_200_seconds": 1},
        },
        "modules": [{"domain": "multimodal", "label": "多模态与文件输入", "baseline_score": "100.00",
                     "baseline_scored": 1, "current_score": "100.00", "current_scored": 1, "total": 1,
                     "paired_count": 1, "paired_baseline_score": "100.00", "paired_current_score": "100.00",
                     "paired_delta": "0.00", "definition_changed_cases": []}],
        "cases": [sample_case()],
    }


class ProductionCompareTemplateTests(unittest.TestCase):
    def test_markdown_uses_module_sections(self):
        text = markdown(sample_report())
        self.assertIn("## 多模态与文件输入（multimodal）", text)
        self.assertIn("report.html", text)
        self.assertNotIn("## Case 明细", text)
        self.assertNotIn("版本不可直接比较：。", text)
        self.assertNotIn("Case 定义变化：。", text)

    def test_html_is_self_contained_and_has_four_views_without_status_matrix(self):
        text = render_html(sample_report())
        self.assertNotIn("/*__CHART_JS__*/", text)
        self.assertNotIn("/*__REPORT_DATA__*/", text)
        for element_id in ("module-heatmap", "score-chart", "duration-chart", "cumulative-chart"):
            self.assertIn('id="' + element_id + '"', text)
        self.assertNotIn('id="status-matrix"', text)
        self.assertNotIn("Case 状态迁移矩阵", text)
        self.assertIn("const report = {", text)

    def test_display_uses_project_rounding(self):
        self.assertEqual(display("78.125"), "78.13")
        self.assertEqual(display("3.845"), "3.85")


if __name__ == "__main__":
    unittest.main()
