"""Compare two production runs with one fixed Markdown and HTML report contract."""

import json
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from pathlib import Path

from .runs import DOMAIN_LABELS, environment_summary, load_results, load_suite


SCHEMA_VERSION = "j2-checkpoint-run-comparison-v1"


def display(value):
    if value is None:
        return None
    return format(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def average(values):
    values = list(values)
    return sum(values, Decimal(0)) / len(values) if values else None


def case_status(case):
    return case["verdict"] or case["excluded_reason"]


def stable_versions(case):
    return (bool((case.get("qa_version_start") or {}).get("version")) and
            bool((case.get("qa_version_start") or {}).get("git_commit")) and
            case.get("qa_version_start") == case.get("qa_version_end"))


def attempt_metadata(run, case_id, executor="functional"):
    batch = json.loads((run / "batch-result.json").read_text())
    selected = next((r.get("selected_attempt", 1) for r in batch.get("cases", [])
                     if r.get("case_id") == case_id), 1)
    if type(selected) is not int or selected < 1:
        return {}
    path = (run / "case-runs" / case_id / "attempts" / case_id /
            ("attempt-%02d" % selected) / "self-benchmark.json")
    return json.loads(path.read_text()) if path.is_file() else {}


def build_comparison(suite_path, baseline_run, current_run, *, allow_suite_projection=False):
    suite_path = Path(suite_path).resolve()
    runs = {"current": Path(current_run).resolve()}
    if baseline_run is not None:
        runs["baseline"] = Path(baseline_run).resolve()
    suite = load_suite(suite_path)
    summaries, results, batches = {}, {}, {}
    for label, run in runs.items():
        batches[label] = json.loads((run / "batch-result.json").read_text(encoding="utf-8"))
        results[label] = load_results(run)
        summaries[label] = environment_summary(suite["cases"], results[label], batches[label], suite["_registry"])
    by_id = {label: {case["case_id"]: case for case in summary["cases"]}
             for label, summary in summaries.items()}
    if allow_suite_projection and baseline_run is not None and any(case_id not in results["baseline"] or case_id not in results["current"]
                                      for case_id in [item["id"] for item in suite["cases"]]):
        raise ValueError("suite_projection_requires_complete_case_coverage")
    same_context = baseline_run is None or all(
        batches["baseline"].get(k) and batches["baseline"].get(k) == batches["current"].get(k)
        for k in ("environment", "workspace_id", "model"))
    rows = []
    for item in suite["cases"]:
        case_id = item["id"]
        new = by_id["current"][case_id]
        executor = item.get("executor", "functional")
        new_meta = attempt_metadata(runs["current"], case_id, executor)
        if baseline_run is None:
            old = {
                "case_id": case_id, "domain": item["domain"], "scope": item.get("scope"),
                "title": new["title"], "execution_status": "not_run", "ui_path": None,
                "score": None, "verdict": None, "excluded_reason": "no_baseline",
                "coverage_eligible": False, "normal_completed": False,
                "expected_checks": None, "actual_checks": None, "passed_checks": None,
                "forced_after_timeout": False, "assertions_complete": False,
                "qa_version_start": None, "qa_version_end": None, "harness_errors": [],
                "evidence_path": None, "selected_attempt": None, "attempts": [],
                "task_seconds": None,
            }
            old_meta = {}
            same_definition = None
            same_suite = None
            same_scoring = None
            same_persistent_context = None
            comparison_status = "no_baseline"
        else:
            old = by_id["baseline"][case_id]
            old_meta = attempt_metadata(runs["baseline"], case_id, executor)
            same_definition = (bool(old_meta.get("case_sha256")) and
                               old_meta.get("case_sha256") == new_meta.get("case_sha256") and
                               old_meta.get("case_sha256") == new.get('case_definition_sha256'))
            same_suite = (allow_suite_projection or
                          bool(old_meta.get("suite_sha256")) and old_meta.get("suite_sha256") == new_meta.get("suite_sha256"))
            same_scoring = (old.get("checkpoint_registry_sha256") == new.get("checkpoint_registry_sha256") and
                            old.get("scoring_policy") == new.get("scoring_policy") and
                            old.get("source_scoring_policy") == new.get("source_scoring_policy"))
            same_persistent_context = (
                old.get("persistent_mode") == new.get("persistent_mode") and
                old.get("persistent_history_context") == new.get("persistent_history_context"))
            if executor == 'persistent':
                same_persistent_context = (same_persistent_context and
                                           bool(old.get('persistent_mode')) and
                                           bool(old.get('persistent_history_context')))
        if baseline_run is None:
            pass
        elif not same_context:
            comparison_status = "execution_context_changed"
        elif not stable_versions(old) or not stable_versions(new):
            comparison_status = "version_mismatch"
        elif not same_definition:
            comparison_status = "case_definition_changed"
        elif not same_scoring:
            comparison_status = "scoring_config_changed"
        elif not same_persistent_context:
            comparison_status = "persistent_context_changed"
        elif not same_suite:
            comparison_status = "suite_changed"
        elif old["score"] is None or new["score"] is None:
            comparison_status = "score_unavailable"
        else:
            comparison_status = "comparable"
        score_delta = (Decimal(new["score"]) - Decimal(old["score"])
                       if comparison_status == "comparable" else None)
        old_seconds = results["baseline"].get(case_id, {}).get("task_seconds") if baseline_run is not None else None
        new_seconds = results["current"].get(case_id, {}).get("task_seconds")
        identity_comparable = (baseline_run is not None and same_context and same_definition and same_suite and
                               same_scoring and same_persistent_context and
                               stable_versions(old) and stable_versions(new))
        duration_delta = (round(new_seconds - old_seconds, 3)
                          if identity_comparable and isinstance(old_seconds, (int, float)) and
                          isinstance(new_seconds, (int, float)) else None)
        rows.append({
            "case_id": case_id, "domain": item["domain"], "title": old["title"],
            "same_execution_context": same_context,
            "same_case_definition": same_definition,
            "same_scoring_config": same_scoring,
            "baseline_checkpoint_registry_sha256": old.get("checkpoint_registry_sha256"),
            "current_checkpoint_registry_sha256": new.get("checkpoint_registry_sha256"),
            "same_persistent_context": same_persistent_context,
            "baseline_case_sha256": old_meta.get("case_sha256"),
            "current_case_sha256": new_meta.get("case_sha256"),
            "comparison_status": comparison_status,
            "score_delta_current_minus_baseline": display(score_delta),
            "task_seconds_delta_current_minus_baseline": duration_delta,
            "baseline": {**old, "task_seconds": old_seconds},
            "current": {**new, "task_seconds": new_seconds},
        })
    modules = []
    for domain in dict.fromkeys(item["domain"] for item in suite["cases"]):
        selected = [row for row in rows if row["domain"] == domain]
        paired = [row for row in selected if row["comparison_status"] == "comparable"]
        old_domain = (next(d for d in summaries["baseline"]["domains"] if d["domain"] == domain)
                      if baseline_run is not None else {
                          "score": None, "scored": 0, "total": len(selected), "coverage": "0.00"})
        new_domain = next(d for d in summaries["current"]["domains"] if d["domain"] == domain)
        paired_old = average(Decimal(row["baseline"]["score"]) for row in paired)
        paired_new = average(Decimal(row["current"]["score"]) for row in paired)
        modules.append({
            "domain": domain, "label": DOMAIN_LABELS.get(domain, domain),
            "baseline_score": old_domain["score"], "baseline_scored": old_domain["scored"],
            "current_score": new_domain["score"], "current_scored": new_domain["scored"],
            "total": old_domain["total"], "paired_count": len(paired),
            "paired_baseline_score": display(paired_old), "paired_current_score": display(paired_new),
            "paired_delta": display(paired_new - paired_old) if paired else None,
            "definition_changed_cases": [row["case_id"] for row in selected
                                         if row["same_case_definition"] is False],
        })
    transitions = Counter((case_status(row["baseline"]), case_status(row["current"])) for row in rows)
    thresholds = {}
    for label in (("baseline", "current") if baseline_run is not None else ("current",)):
        seconds = [row[label]["task_seconds"] for row in rows]
        thresholds[label] = {
            "total_cases": len(rows),
            "numeric_task_seconds": sum(isinstance(value, (int, float)) for value in seconds),
            "within_100_seconds": sum(isinstance(value, (int, float)) and value <= 100 for value in seconds),
            "within_200_seconds": sum(isinstance(value, (int, float)) and value <= 200 for value in seconds),
        }
    if baseline_run is None:
        thresholds["baseline"] = {
            "total_cases": len(rows), "numeric_task_seconds": 0,
            "within_100_seconds": 0, "within_200_seconds": 0,
        }
    summary_keys = (
        "versions", "scored", "total", "coverage", "normal_completed", "normal_completion_coverage",
        "passed", "partial", "failed", "timeouts", "framework_errors", "blocked",
    )
    baseline_summary = ({key: summaries["baseline"][key] for key in summary_keys}
                        if baseline_run is not None else {
                            "versions": [], "scored": 0, "total": len(rows), "coverage": "0.00",
                            "normal_completed": 0, "normal_completion_coverage": "0.00",
                            "passed": 0, "partial": 0, "failed": 0, "timeouts": 0,
                            "framework_errors": 0, "blocked": 0,
                        })
    return {
        "schema_version": SCHEMA_VERSION, "suite_id": suite["suite_id"],
        "model": batches["current"]["model"],
        "excluded_scenarios": suite.get("excluded_scenarios", []),
        "baseline_available": baseline_run is not None,
        "baseline": {"run": runs["baseline"].name if baseline_run is not None else "no_baseline",
                     **baseline_summary},
        "current": {"run": runs["current"].name,
                    **{key: summaries["current"][key] for key in summary_keys},
                    "max_workers": batches["current"].get("max_workers"),
                    "derived_from": batches["current"].get("derived_from")},
        "comparability": {
            "suite_sha256": attempt_metadata(runs["current"], suite["cases"][0]["id"]).get("suite_sha256"),
            "suite_projection": {
                "enabled": allow_suite_projection,
                "baseline_original_suite_sha256": (attempt_metadata(
                    runs["baseline"], suite["cases"][0]["id"]).get("suite_sha256")
                    if baseline_run is not None else None),
                "current_original_suite_sha256": attempt_metadata(
                    runs["current"], suite["cases"][0]["id"]).get("suite_sha256"),
            },
            "no_baseline": [row["case_id"] for row in rows if row["comparison_status"] == "no_baseline"],
            "case_definition_changed": [row["case_id"] for row in rows
                                        if row["same_case_definition"] is False],
            "scoring_config_changed": [row["case_id"] for row in rows
                                       if row["comparison_status"] == "scoring_config_changed"],
            "persistent_context_changed": [row["case_id"] for row in rows
                                           if row["comparison_status"] == "persistent_context_changed"],
            "version_mismatch": [row["case_id"] for row in rows
                                 if row["comparison_status"] == "version_mismatch"],
            "score_unavailable": [row["case_id"] for row in rows
                                  if row["comparison_status"] == "score_unavailable"],
            "fully_comparable_scored_cases": sum(row["comparison_status"] == "comparable" for row in rows),
            "current_version_changed_during_run": len(summaries["current"]["versions"]) > 1,
        },
        "status_transitions": [{"baseline": old, "current": new, "count": count}
                               for (old, new), count in sorted(transitions.items())],
        "duration_thresholds": thresholds, "modules": modules, "cases": rows,

    }


def markdown(report):
    def score(value):
        return value if value is not None else "—"

    def seconds(value):
        return str(value) if value is not None else "—"

    def checks(case):
        passed, expected = case.get("passed_checks"), case.get("expected_checks")
        return f"{passed}/{expected}" if passed is not None and expected is not None else "—"

    def version(case):
        value = case.get("qa_version_start") or {}
        label = value.get("git_commit") or value.get("version") or "—"
        if case.get("qa_version_start") != case.get("qa_version_end"):
            label += "（结束不一致）"
        return label

    baseline_label = ("`{}`".format(report["baseline"]["run"])
                      if report.get("baseline_available", True) else "无（首轮新基线）")
    lines = [
        "# 生产 Case 前后运行对比" if report.get("baseline_available", True) else "# 生产 Case 首轮基线", "",
        "> 基线：{}；当前：`{}`。模型为 `{}`。".format(
            baseline_label, report["current"]["run"], report["model"]),
        "> 本报告按能力模块分别展示，只呈现结果和差值，不自动判定回退，也不计算 MOI 整体质量分。", "",
        "## 比较限制", "",
        "- 本次生产版本：" + " / ".join(report["current"]["versions"]) + "；每条 case 的实际版本在所属模块中展示。",
    ]
    for excluded in report.get("excluded_scenarios", []):
        lines.append("- 暂不评测：" + "、".join(excluded.get("domains", [])) + "（" +
                     "、".join(excluded.get("cases", [])) + "）；不计入覆盖率分母。")
    if report["comparability"].get("suite_projection", {}).get("enabled"):
        lines.append("- 对比范围投影：只比较当前套件指定的 case；要求两侧都有结果，并继续核对定义及评分身份。")
    if report["current"].get("derived_from"):
        derived = report["current"]["derived_from"]
        lines.append("- 当前结果由多个 Run 派生合并：基础 Run `" +
                     Path(derived["base_run"]).name + "`，复跑 Run `" +
                     Path(derived["recheck_run"]).name + "`；采用复跑证据的 case：" +
                     "、".join(derived["replaced_cases"]) + "。原始 blocked 结果仍保留在来源 Run 中。")
    if report["comparability"]["version_mismatch"]:
        lines.append("- 版本不可直接比较：" + "、".join(report["comparability"]["version_mismatch"]) + "。")
    if report["comparability"]["case_definition_changed"]:
        lines.append("- Case 定义变化：" + "、".join(report["comparability"]["case_definition_changed"]) +
                     "；这些 case 不计算直接分差。")
    if report["comparability"].get("scoring_config_changed"):
        lines.append("- 检查点评分配置变化：" + "、".join(report["comparability"]["scoring_config_changed"]) +
                     "；这些 case 不计算直接分差。")
    if report["comparability"].get("persistent_context_changed"):
        lines.append("- 持久会话上下文变化：" + "、".join(report["comparability"]["persistent_context_changed"]) +
                     "；mode 或历史上下文不同，不计算直接分差。")
    for row in report["cases"]:
        old, new = row["baseline"], row["current"]
        if old.get("persistent_mode") or new.get("persistent_mode"):
            lines.append("- 持久上下文 `{}`：上次 `{}/sequence={}/{}`；本次 `{}/sequence={}/{}`。".format(
                row["case_id"], old.get("persistent_mode"), old.get("persistent_sequence"),
                old.get("persistent_history_context"), new.get("persistent_mode"),
                new.get("persistent_sequence"), new.get("persistent_history_context")))
    lines += ["- 模块原始分受计分覆盖影响；同口径均分只使用定义一致、版本完整且两次均出分的 case。", "",
              "## 运行概览", "",
              "| 运行 | 生产版本 | 计分覆盖 | 正常完成 | PASS | PARTIAL | FAIL | timeout | blocked/排除 |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for key, title in (("baseline", "上次生产"), ("current", "本次生产")):
        if key == "baseline" and not report.get("baseline_available", True):
            continue
        summary = report[key]
        lines.append("| {} | {} | {}/{} ({}%) | {}/{} ({}%) | {} | {} | {} | {} | {} |".format(
            title, " / ".join(summary["versions"]), summary["scored"], summary["total"], summary["coverage"],
            summary["normal_completed"], summary["total"], summary["normal_completion_coverage"],
            summary["passed"], summary["partial"], summary["failed"], summary["timeouts"],
            summary["total"] - summary["scored"]))
    lines += ["", "本次执行并发：`max_workers={}`；不同领域最多两路，同领域串行；知识库最后单路串行。".format(
        report["current"].get("max_workers")), "", "## 模块总览", "",
        "| 模块 | 上次原始分（覆盖） | 本次原始分（覆盖） | 同口径 Case 数 | 上次同口径均分 | 本次同口径均分 | 差值 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for module in report["modules"]:
        lines.append("| {} | {}（{}/{}） | {}（{}/{}） | {} | {} | {} | {} |".format(
            module["label"], score(module["baseline_score"]), module["baseline_scored"], module["total"],
            score(module["current_score"]), module["current_scored"], module["total"], module["paired_count"],
            score(module["paired_baseline_score"]), score(module["paired_current_score"]),
            score(module["paired_delta"])))
    lines += ["", "## 耗时阈值", "", "| 运行 | 有任务耗时 | 100 秒内完成 | 200 秒内完成 |",
              "| --- | ---: | ---: | ---: |"]
    for key, title in (("baseline", "上次生产"), ("current", "本次生产")):
        if key == "baseline" and not report.get("baseline_available", True):
            continue
        value = report["duration_thresholds"][key]
        lines.append("| {} | {}/{} | {}/{} | {}/{} |".format(
            title, value["numeric_task_seconds"], value["total_cases"], value["within_100_seconds"],
            value["total_cases"], value["within_200_seconds"], value["total_cases"]))
    lines += ["", "分母保留全部 case；没有有效任务耗时的 case 不计入阈值内完成数。"]
    for module in report["modules"]:
        cases = [case for case in report["cases"] if case["domain"] == module["domain"]]
        lines += ["", "## {}（{}）".format(module["label"], module["domain"]), "",
                  "**模块结果**：上次原始分 {}（{}/{}）；本次原始分 {}（{}/{}）。".format(
                      score(module["baseline_score"]), module["baseline_scored"], module["total"],
                      score(module["current_score"]), module["current_scored"], module["total"])]
        if module["paired_count"]:
            lines.append("**同口径对比**：{} 条 case，均分 {} → {}，差值 {}。".format(
                module["paired_count"], module["paired_baseline_score"], module["paired_current_score"],
                module["paired_delta"]))
        else:
            lines.append("**同口径对比**：没有满足全部条件的 case，不计算直接分差。")
        if module["definition_changed_cases"]:
            lines.append("**定义变化**：" + "、".join(module["definition_changed_cases"]) + "；这些 case 仅并列展示。")
        transitions = Counter((case_status(case["baseline"]), case_status(case["current"])) for case in cases)
        lines.append("**状态迁移**：" + "；".join(
            f"{old} → {new} {count} 条" for (old, new), count in sorted(transitions.items())) + "。")
        lines += ["", "| Case | 简介 | 上次分 | 本次分 | 分差 | 上次结果 | 本次结果 | 上次检查点 | 本次检查点 | 上次秒数 | 本次秒数 | 秒数差 | 本次版本 | 比较状态 |",
                  "| --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |"]
        for case in cases:
            old, new = case["baseline"], case["current"]
            lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                case["case_id"], case["title"], score(old["score"]), score(new["score"]),
                score(case["score_delta_current_minus_baseline"]), case_status(old), case_status(new),
                checks(old), checks(new), seconds(old["task_seconds"]), seconds(new["task_seconds"]),
                seconds(case["task_seconds_delta_current_minus_baseline"]), version(new),
                case["comparison_status"]))
    lines += ["", "## 证据", "", "- 结构化对比：`comparison.json`。", "- 图表报告：`report.html`。", ""]
    return "\n".join(lines)
