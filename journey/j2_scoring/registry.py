"""Resolve existing logical checkpoint identities without running a case."""
import copy
from collections import Counter, defaultdict
import hashlib
import json
from journey.catalog import ROOT, load_case
from .score import expected_check_count, sha256
SCHEMA_VERSION = "journey-checkpoint-registry-v1"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

def value_hash(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def case_entry(registry, case_id):
    matches = [case for case in (registry or {}).get("cases", []) if case.get("case_id") == case_id]
    if len(matches) > 1:
        raise ValueError("duplicate_checkpoint_registry_case:" + case_id)
    return matches[0] if matches else None

def case_registry_hash(registry, case_id):
    entry = case_entry(registry, case_id)
    return value_hash(entry) if entry is not None else None

def validate_registry(registry):
    if registry.get("schema_version") != SCHEMA_VERSION or registry.get("real_execution") is not False:
        raise ValueError("invalid_checkpoint_registry_header")
    ids = []
    for entry in registry.get("cases", []):
        case_id = entry.get("case_id")
        case = load_case(case_id)
        path = ROOT / "cases" / ("journey-" + case_id[1]) / case_id / "case.json"
        if entry.get("case_sha256") != sha256(path):
            raise ValueError("checkpoint_registry_case_changed:" + case_id)
        expected = expected_check_count(case)
        checkpoints = entry.get("checkpoints", [])
        if entry.get("checkpoint_count") != expected or len(checkpoints) != expected:
            raise ValueError("checkpoint_registry_count_mismatch:" + case_id)
        ids.extend(check.get("checkpoint_id") for check in checkpoints)
    if len(ids) != len(set(ids)) or any(not isinstance(value, str) or not value for value in ids):
        raise ValueError("invalid_or_duplicate_checkpoint_id")
    return registry

def matches(rule, definition):
    if "rule" in definition:
        return rule == definition["rule"]
    if "rule_any" in definition:
        return rule in definition["rule_any"]
    return (rule.startswith(definition["rule_prefix"]) and
            all(fragment in rule for fragment in definition.get("rule_contains", [])))

def match_key(session, definition):
    return canonical({"session": session, "match": definition}).decode("utf-8")

def resolve_checkpoints(registry, case_id, checks):
    entry = case_entry(registry, case_id)
    if entry is None:
        return copy.deepcopy(checks), {"status": "not_registered"}
    expected = entry["checkpoints"]
    by_key = defaultdict(list)
    for checkpoint in expected:
        by_key[match_key(checkpoint["session"], checkpoint["match"])].append(checkpoint)
    occurrences = Counter()
    resolved, placeholders = [], []
    for index, check in enumerate(copy.deepcopy(checks)):
        if check.get("detail") == "checkpoint_not_reached_or_unavailable":
            placeholders.append((index, check))
            continue
        rule = check.get("rule")
        keys = [key for key in by_key if matches(rule, json.loads(key)["match"]) and
                json.loads(key)["session"] == check.get("session")]
        if len(keys) != 1:
            raise ValueError("checkpoint_registry_rule_mismatch:" + case_id + ":" + str(rule))
        key = keys[0]
        occurrences[key] += 1
        candidates = by_key[key]
        if occurrences[key] > len(candidates):
            raise ValueError("checkpoint_registry_duplicate_rule:" + case_id + ":" + str(rule))
        check["checkpoint_id"] = candidates[occurrences[key] - 1]["checkpoint_id"]
        check["checkpoint_id_source"] = "registry_rule_occurrence"
        resolved.append((index, check))
    assigned = {check["checkpoint_id"] for _, check in resolved}
    remaining = [checkpoint for checkpoint in expected if checkpoint["checkpoint_id"] not in assigned]
    if len(remaining) != len(placeholders):
        raise ValueError("checkpoint_registry_unresolved_count:" + case_id)
    for (_, check), checkpoint in zip(placeholders, remaining):
        check["checkpoint_id"] = checkpoint["checkpoint_id"]
        check["checkpoint_id_source"] = "unavailable_registry_remainder"
    checks = [check for _, check in sorted(resolved + placeholders, key=lambda item: item[0])]
    return checks, {
        "status": "resolved",
        "checkpoint_registry_sha256": case_registry_hash(registry, case_id),
        "rule_resolved": len(resolved),
        "unavailable_remainder_resolved": len(placeholders),
    }
