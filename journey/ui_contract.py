"""Evidence-driven selection between A2UI and MCP App UI paths."""

import json


A2UI = "a2ui"
MCP_APP = "mcp_app"
MIXED_PATH = "mixed_path"
UNSUPPORTED_PATH = "unsupported_path"
UI_PATHS = {A2UI, MCP_APP}


def mcp_resource_uri(payload):
    if not isinstance(payload, dict):
        return None
    value = payload.get("mcp_app_resource_uri") or payload.get("mcpAppResourceUri")
    return value if isinstance(value, str) and value.startswith("ui://") else None


def successful_mcp_present(event):
    payload = event.get("payload")
    if event.get("kind") != "tool_result" or not isinstance(payload, dict):
        return False
    if payload.get("success") is not True:
        return False
    tool_id = payload.get("tool_id") or payload.get("tool")
    return tool_id == "present_mcp_app" and mcp_resource_uri(payload) is not None


def detect_ui_path(events_path, task_ids):
    """Select exactly one path from task-bound evidence, never model prose."""
    task_ids = set(task_ids)
    if not events_path.is_file():
        return UNSUPPORTED_PATH
    has_a2ui, has_mcp_app = False, False
    for line in events_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("task_id") not in task_ids:
            continue
        has_a2ui = has_a2ui or event.get("kind") == "a2ui"
        has_mcp_app = has_mcp_app or successful_mcp_present(event)
    if has_a2ui and has_mcp_app:
        return MIXED_PATH
    if has_a2ui:
        return A2UI
    if has_mcp_app:
        return MCP_APP
    return UNSUPPORTED_PATH


def validate_ui_contract(case):
    contract = case.get("ui_contract")
    if contract is None:
        return None
    if (not isinstance(contract, dict) or set(contract) != {"allowed_paths", "selection", "mixed_path"} or
            not isinstance(contract["allowed_paths"], list) or not contract["allowed_paths"] or
            any(path not in UI_PATHS for path in contract["allowed_paths"]) or
            len(set(contract["allowed_paths"])) != len(contract["allowed_paths"]) or
            contract["selection"] != "first_valid_evidence" or contract["mixed_path"] != "fail"):
        raise ValueError("invalid_ui_contract")
    return contract


def path_allowed(case, path):
    contract = validate_ui_contract(case)
    return contract is not None and path in contract["allowed_paths"]
