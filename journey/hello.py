"""Run with python -m journey.hello; configuration comes only from environment."""

import json
import os
import time
import urllib.parse
import uuid

from .client import Client, Failure, segment
from .a2a import final_reply

TERMINAL = {"completed", "failed", "canceled", "rejected"}
ACTIVE = {"submitted", "working", "input-required", "auth-required"}


def unique(items, field, value, label):
    matches = [item for item in items if item.get(field) == value]
    if len(matches) != 1:
        raise Failure(label + "_not_unique")
    return matches[0]


def discover(client, workspace_name):
    query = urllib.parse.urlencode({"name": workspace_name})
    data = client.product("GET", "/workspaces?" + query, expected_code="OK")
    workspace = unique(data["workspaces"], "name", workspace_name, "workspace")
    if workspace["status"] != "ACTIVE":
        raise Failure("workspace_inactive")
    workspace_id = workspace["id"]
    client.headers["X-Workspace-ID"] = workspace_id
    root = "/workspaces/" + segment(workspace_id)
    agents = []
    for _ in range(100):
        page = client.product("GET", root + "/agents?" + urllib.parse.urlencode({
            "limit": 100, "offset": len(agents),
        }))
        agents.extend(page["items"])
        if len(agents) >= page["total"]:
            break
        if not page["items"]:
            raise Failure("invalid_pagination")
    else:
        raise Failure("agent_inventory_too_large")
    agent = unique(agents, "source_ref", "agent-zero-builder", "momo")
    if agent["status"] != "active":
        raise Failure("momo_inactive")
    return root, {"agent_id": agent["id"], "agent_workspace_id": agent["workspace_id"]}


def task_state(task, conversation_id, task_id=None):
    if (task.get("kind") != "task" or task.get("contextId") != conversation_id
            or not isinstance(task.get("id"), str) or not task["id"]
            or (task_id is not None and task["id"] != task_id)):
        raise Failure("task_identity_mismatch")
    state = task["status"]["state"]
    if state not in TERMINAL | ACTIVE:
        raise Failure("unknown_task_state")
    return state


def assert_reply(task, marker):
    if marker not in final_reply(task):
        raise Failure("reply_mismatch")


def run(client, workspace_name, model):
    started = time.monotonic()
    run_id = str(uuid.uuid4())
    report = {"case": "momo-hello", "run_id": run_id, "status": "failed",
              "stage": "discovery", "cleanup": "not_needed"}
    conversation_id = None
    task_id = None
    state = None
    try:
        root, selector = discover(client, workspace_name)
        report["stage"] = "create_conversation"
        # A caller-generated ID also permits diagnosis when the creation response is lost.
        conversation_id = str(uuid.uuid4())
        created = client.product("POST", root + "/conversations", {
            "id": conversation_id, **selector,
            "title": "journey-momo-hello-" + run_id, "status": "active",
        })
        if created.get("id") != conversation_id:
            raise Failure("conversation_identity_mismatch")
        report["stage"] = "send_message"
        marker = "HELLO_MOMO_" + run_id
        task_started = time.monotonic()
        task = client.rpc(selector, "message/send", {
            "message": {"kind": "message", "role": "user",
                        "messageId": str(uuid.uuid4()), "contextId": conversation_id,
                        "parts": [{"kind": "text", "text":
                                   "Hello Momo! Reply with exactly this text: " + marker}]},
            "model": model, "idempotencyKey": run_id,
        })
        state = task_state(task, conversation_id)
        task_id = task["id"]
        report["stage"] = "read_task"
        # Always read the persisted result, even when send returns completed.
        while True:
            task = client.rpc(selector, "tasks/get", {"id": task_id})
            state = task_state(task, conversation_id, task_id)
            if state not in {"submitted", "working"}:
                break
            if client.deadline - time.monotonic() <= 1:
                raise Failure("deadline_exceeded")
            time.sleep(1)
        report["task_seconds"] = round(time.monotonic() - task_started, 3)
        report["stage"] = "assert_reply"
        assert_reply(task, marker)
        report["status"] = "passed"
        report["stage"] = "complete"
    except Failure as error:
        report["error"] = error.code
        if error.http_status is not None:
            report["http_status"] = error.http_status
    except (KeyError, TypeError, AttributeError, ValueError):
        report["error"] = "invalid_response"
    finally:
        if conversation_id is not None:
            # Cleanup has its own bounded budget; never resend the prompt.
            client.deadline = time.monotonic() + 20
            report["cleanup"] = "incomplete"
            try:
                if task_id and state not in TERMINAL:
                    stopped = client.rpc(selector, "tasks/cancel", {"id": task_id})
                    state = task_state(stopped, conversation_id, task_id)
                    if state not in TERMINAL:
                        raise Failure("cancel_not_confirmed")
                # With an unknown send outcome, do not mistake archiving for cancellation.
                if report["stage"] == "send_message" and not task_id:
                    raise Failure("task_outcome_unknown")
                path = root + "/conversations/" + segment(conversation_id)
                path += "?" + urllib.parse.urlencode(selector)
                archived = client.product("PATCH", path, {"status": "archived"})
                if archived.get("status") != "archived":
                    raise Failure("archive_not_confirmed")
                report["cleanup"] = "archived"
            except Failure as error:
                report["cleanup_error"] = error.code
            except (KeyError, TypeError, AttributeError, ValueError):
                report["cleanup_error"] = "invalid_response"
            if report["cleanup"] != "archived":
                report["status"] = "failed"
        report["total_seconds"] = round(time.monotonic() - started, 3)
        report["requests"] = client.requests
    return report


def main():
    try:
        client = Client(os.environ["MOI_BASE_URL"], os.environ["MOI_PAT"])
        report = run(client, os.environ["MOI_WORKSPACE"], os.environ["MOI_MODEL"])
    except (KeyError, Failure):
        report = {"case": "momo-hello", "status": "failed", "error": "invalid_configuration"}
    except Exception:
        # Public CI must never include a traceback containing server data or credentials.
        report = {"case": "momo-hello", "status": "failed", "error": "internal_error"}
    output = json.dumps(report, sort_keys=True)
    print(output)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write("### Momo hello\n\n```json\n" + output + "\n```\n")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
