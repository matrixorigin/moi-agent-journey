"""Validate MCP App resources, DOM structure and real UI interaction."""

import hashlib
import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path

from .client import Failure, segment
from .evidence import utc_now
from .ui_contract import mcp_resource_uri


MAX_HTML_BYTES = 2 * 1024 * 1024


def expected_mcp_check_count(turn):
    component = turn.get("a2ui_evidence", {}).get("component")
    if component in {"Chart", "Table"}:
        return 6
    if component == "Question":
        return 9
    if component == "Form":
        return 12 if turn.get("a2ui_form_submit") else 8
    raise Failure("invalid_mcp_app_case")


class ExternalDependencies(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.references.append("script:" + attrs["src"])
        if tag == "link" and "stylesheet" in attrs.get("rel", "").lower() and attrs.get("href"):
            self.references.append("stylesheet:" + attrs["href"])


def mcp_uris(events_path, task_id):
    uris = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("task_id") != task_id or event.get("kind") != "tool_result":
            continue
        payload = event.get("payload", {})
        if (payload.get("tool_id") or payload.get("tool")) == "present_mcp_app" and payload.get("success") is True:
            uri = mcp_resource_uri(payload)
            if uri:
                uris.append(uri)
    return uris


def fetch_resource(execution, task_id, uri):
    result = execution.client.product(
        "POST", execution.root + "/agent-runtime-tasks/" + segment(task_id) + "/mcp-apps/resource",
        {"tool_id": "present_mcp_app", "uri": uri})
    contents = result.get("contents")
    if (result.get("uri") != uri or not isinstance(contents, list) or len(contents) != 1 or
            not isinstance(contents[0], dict)):
        raise Failure("mcp_app_resource_identity_mismatch")
    resource = contents[0]
    if resource.get("uri") != uri or resource.get("mimeType") != "text/html;profile=mcp-app":
        raise Failure("mcp_app_resource_identity_mismatch")
    text = resource.get("text")
    if not isinstance(text, str):
        raise Failure("mcp_app_resource_not_html")
    return text


def self_contained_errors(html):
    errors = []
    if len(html.encode("utf-8")) > MAX_HTML_BYTES:
        errors.append("html_too_large")
    for marker in ("/*__STYLES__*/", "/*__SCRIPT__*/", "/*__CSS__*/", "/*__JS__*/"):
        if marker in html:
            errors.append("unresolved_placeholder:" + marker)
    parser = ExternalDependencies()
    parser.feed(html)
    errors.extend("external_dependency:" + item for item in parser.references)
    if "<html" not in html.lower() and "<!doctype html" not in html.lower():
        errors.append("html_document_missing")
    return errors


def run_dom_checks(root, html_path, expected, submission):
    script = Path(__file__).resolve().parent.parent / "scripts" / "mcp_app_dom.mjs"
    contract = {"expected": expected, "submission": submission,
                "resource_uri": expected.get("resource_uri")}
    process = subprocess.run(["node", str(script), str(html_path)], input=json.dumps(contract),
                             text=True, capture_output=True, timeout=30)
    if process.returncode != 0:
        return [{"rule": "无头 DOM 可执行并检查交互", "passed": False,
                 "detail": "dom_execution_failed:" + (process.stderr.strip().splitlines() or ["unknown"])[-1]}]
    try:
        line = next(line for line in reversed(process.stdout.strip().splitlines())
                    if line.startswith("__MCP_APP_CHECKS__"))
        result = json.loads(line[len("__MCP_APP_CHECKS__"):])
    except (ValueError, StopIteration):
        return [{"rule": "无头 DOM 可执行并检查交互", "passed": False, "detail": "dom_result_invalid"}]
    if not isinstance(result, list) or any(not isinstance(item, dict) or type(item.get("passed")) is not bool
                                           for item in result):
        return [{"rule": "无头 DOM 可执行并检查交互", "passed": False, "detail": "dom_result_invalid"}]
    return result


def evaluate_mcp_app(execution, task, session_id, turn, variables, submission_turn=None):
    """Return MCP-specific checks; resource/API failures remain framework errors."""
    task_id = task["task_id"]
    events_path = execution.evidence.root / "events.jsonl"
    uris = mcp_uris(events_path, task_id)
    expected = turn.get("a2ui_evidence", {})
    expected = {**expected, "fields": {**expected.get("fields", {}), "resource_uri": uris[0] if uris else None}}
    submission = None
    if submission_turn:
        submission = {key: value for key, value in submission_turn.items()
                      if key in {"a2ui_submit", "a2ui_form_submit", "json_fields"}}
        submission["variables"] = variables
    checks = [{"session": session_id, "rule": "MCP App 成功展示并返回 ui:// 资源", "passed": bool(uris),
               "detail": "" if uris else "mcp_app_resource_uri_missing"}]
    if not uris:
        return checks
    html = fetch_resource(execution, task_id, uris[0])
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
    execution.evidence.write("sessions/" + session_id + "/mcp-app-resource.json", {
        "task_id": task_id, "uri": uris[0], "sha256": digest, "bytes": len(html.encode("utf-8")),
        "recorded_at": utc_now()})
    html_path = execution.evidence.root / "sessions" / session_id / "mcp-app.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(execution.evidence.text(html), encoding="utf-8")
    checks.append({"session": session_id, "rule": "MCP App 资源身份、类型和哈希可核验", "passed": True,
                   "detail": ""})
    errors = self_contained_errors(html)
    checks.append({"session": session_id, "rule": "MCP App HTML 自包含且不超过 2 MiB", "passed": not errors,
                   "detail": "" if not errors else ";".join(errors)})
    for check in run_dom_checks(execution.evidence.root, html_path, expected, submission):
        checks.append({"session": session_id, **check})
    checks.append({"session": session_id, "rule": "MCP App 使用单一资源且没有重复创建", "passed": len(uris) == 1,
                   "detail": "" if len(uris) == 1 else "duplicate_mcp_app_resources:" + str(len(uris))})
    return checks
