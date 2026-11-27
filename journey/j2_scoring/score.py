"""Equal-weight Journey scoring without hard gates or gate status."""

import copy
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path


SCHEMA_VERSION = "journey-functional-score-only-v1"


def display(value):
    if value is None:
        return None
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def expected_check_count(case, result=None):
    if isinstance(result, dict) and type(result.get("ui_path_expected_checks")) is int:
        return result["ui_path_expected_checks"]
    if not isinstance(case, dict) or not isinstance(case.get("sessions"), list):
        return None
    total = 0
    for session in case["sessions"]:
        if not isinstance(session, dict):
            return None
        if "expected_checks" in session:
            value = session["expected_checks"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            total += value
            continue
        turns = session.get("turns", [])
        if not isinstance(turns, list):
            return None
        for turn in turns:
            if not isinstance(turn, dict) or not isinstance(turn.get("assertions", []), list):
                return None
            total += 1 + len(turn.get("assertions", []))
    return total


def version_blocked(result):
    return any(isinstance(item, dict) and item.get("reason") == "qa_backend_changed_during_case"
               for item in result.get("failures", []))


def unscored_reason(result, checks, malformed_checks, expected, harness_errors):
    original_status = result.get("status")
    if original_status is None:
        functional_state = result.get("functional", {}).get("state")
        if functional_state not in {"error", "blocked"} and result.get("task", {}).get("state") == "timeout":
            original_status = "timeout"
        else:
            original_status = {"pass": "passed", "fail": "failed", "error": "error",
                               "blocked": "blocked"}.get(functional_state, result.get("test_status"))
    if original_status == "blocked" or result.get("blocked") is True or version_blocked(result):
        return "blocked", original_status
    if harness_errors or original_status == "error":
        return "framework_error", original_status
    if original_status == "timeout":
        return "timeout", original_status
    if original_status == "not_run":
        return "not_run", original_status
    if not checks:
        return "empty_checks", original_status
    if malformed_checks:
        return "invalid_checks", original_status
    if expected is not None and len(checks) < expected:
        return "checkpoints_missing", original_status
    if expected is not None and len(checks) > expected:
        return "checkpoints_extra", original_status
    if expected is None:
        return "expected_checks_unavailable", original_status
    if original_status not in {"passed", "failed"}:
        return "execution_incomplete", original_status
    if result.get("qa_version_start") != result.get("qa_version_end"):
        return "blocked", original_status
    return None, original_status


def score_functional_result(result, evidence_path, case=None, case_path=None):
    """Score one complete attempt; unresolved evidence never becomes zero."""
    if not isinstance(result, dict):
        raise ValueError(str(evidence_path) + ": result must be a JSON object")
    evidence_path = Path(evidence_path).resolve()
    checks = result.get("validation", {}).get("checks", [])
    if not isinstance(checks, list):
        checks = []
        malformed_checks = True
    else:
        malformed_checks = any(not isinstance(item, dict) or type(item.get("passed")) is not bool
                               for item in checks)
    expected = expected_check_count(case, result)
    harness_errors = result.get("harness_errors", [])
    reason, original_status = unscored_reason(result, checks, malformed_checks, expected, harness_errors)
    warnings = []
    if case_path is None:
        warnings.append("case_definition_missing")
    elif expected is None:
        warnings.append("expected_checks_unavailable")
    elif len(checks) != expected:
        warnings.append("expected_checks_mismatch:{}!={}".format(expected, len(checks)))

    checkpoints = []
    for index, check in enumerate(checks, 1):
        valid = isinstance(check, dict) and type(check.get("passed")) is bool
        status = "unscored" if not valid else "passed" if check["passed"] else "failed"
        checkpoints.append({
            "index": index,
            "checkpoint_id": check.get("checkpoint_id") if isinstance(check, dict) else None,
            "checkpoint_id_source": check.get("checkpoint_id_source") if isinstance(check, dict) else None,
            "session": check.get("session") if isinstance(check, dict) else None,
            "rule": check.get("rule") if isinstance(check, dict) else None,
            "status": status,
            "weight": "1",
            "weighted_points": None if status == "unscored" else "1" if status == "passed" else "0",
            "detail": check.get("detail") if isinstance(check, dict) else None,
            "evidence": copy.deepcopy(check),
        })

    score = None
    verdict = None
    scoring_status = "unscored"
    if reason is None:
        passed = sum(item["status"] == "passed" for item in checkpoints)
        with localcontext() as context:
            context.prec = 50
            score = display(Decimal(passed) * Decimal(100) / Decimal(len(checkpoints)))
        scoring_status = "scored"
        if Decimal(score) == Decimal(100):
            verdict = "PASS"
        elif Decimal(score) == 0 and all(item["status"] == "failed" for item in checkpoints):
            verdict = "FAIL"
        else:
            verdict = "PARTIAL"

    return {
        "case_id": result.get("case_id"),
        "title": result.get("title"),
        "target_workspace": result.get("target_workspace_id") or result.get("workspace"),
        "run_id": result.get("run_id"),
        "original_status": original_status,
        "ui_path": result.get("ui_path"),
        "stage": result.get("stage"),
        "qa_version_start": copy.deepcopy(result.get("qa_version_start")),
        "qa_version_end": copy.deepcopy(result.get("qa_version_end")),
        "task_seconds": result.get("task_seconds"),
        "checkpoint_registry_sha256": (result.get("always_assert") or {}).get("checkpoint_registry_sha256"),
        "elapsed_seconds": result.get("elapsed_seconds", result.get("qa_seconds")),
        "capture_complete": result.get("capture_complete"),
        "tasks": copy.deepcopy(result.get("tasks", [])),
        "cleanup": copy.deepcopy(result.get("cleanup")),
        "conversations": copy.deepcopy(result.get("conversations", [])),
        "failures": copy.deepcopy(result.get("failures", [])),
        "harness_errors": copy.deepcopy(harness_errors),
        "evidence_path": str(evidence_path),
        "result_sha256": sha256(evidence_path),
        "case_definition_path": str(Path(case_path).resolve()) if case_path else None,
        "expected_checks": expected,
        "actual_checks": len(checks),
        "warnings": warnings,
        "scoring_status": scoring_status,
        "verdict": verdict,
        "reason": reason,
        "score": score,
        "checkpoints": checkpoints,
    }
