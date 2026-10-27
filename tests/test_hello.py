import contextlib
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from journey.client import Client, Failure
from journey.hello import assert_reply, run


@contextlib.contextmanager
def server(mode="success"):
    calls = []
    state = {"reads": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"]))) \
                if self.headers.get("Content-Length") else None
            calls.append((self.command, self.path, body, dict(self.headers)))
            status = 200
            result = None
            if mode == "unauthorized":
                status, result = 401, {"error": "PRIVATE_BODY_do_not_publish"}
            elif mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "/credential-leak-target")
                self.end_headers()
                return
            elif self.path.startswith("/workspaces?"):
                result = {"code": "OK", "data": {"workspaces": [
                    {"name": "journey-1", "id": "ws", "status": "ACTIVE"}]}}
            elif "/agents?" in self.path:
                result = {"code": 0, "data": {"items": [
                    {"id": "momo", "workspace_id": "system", "status": "active",
                     "source_ref": "agent-zero-builder"}], "total": 1}}
            elif self.command == "PATCH":
                status = 500 if mode == "archive_failure" else 200
                result = {"code": 0, "data": {"status": "archived"}}
            elif self.path.endswith("/conversations"):
                state["conversation"] = body["id"]
                result = {"code": 0, "data": {"id": body["id"]}}
            elif self.path == "/agents/a2a":
                method = body["method"]
                if method == "message/send":
                    state["marker"] = body["params"]["message"]["parts"][0]["text"].split()[-1]
                if method == "tasks/get":
                    state["reads"] += 1
                task_status = "completed"
                if mode == "never_complete" or mode == "working" and state["reads"] < 2:
                    task_status = "working"
                elif mode == "failed":
                    task_status = "failed"
                elif mode == "input":
                    task_status = "input-required"
                if method == "tasks/cancel":
                    task_status = "canceled"
                task = {"kind": "task", "id": "task-1", "contextId": state["conversation"],
                        "status": {"state": task_status, "message": {"role": "agent", "parts": [
                            {"kind": "text", "text": state["marker"]}]}}}
                if mode == "wrong_reply" and method == "tasks/get":
                    task["status"]["message"]["parts"][0]["text"] = "PRIVATE_BODY_do_not_publish"
                if mode == "wrong_context" and method == "tasks/get":
                    task["contextId"] = "another-conversation"
                result = {"jsonrpc": "2.0", "id": body["id"], "result": task}
                if mode == "rpc_error":
                    result = {"jsonrpc": "2.0", "id": body["id"],
                              "error": {"message": "PRIVATE_BODY_do_not_publish"}}
                if mode == "wrong_rpc_id":
                    result["id"] = "another-request"
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        do_GET = do_POST = do_PATCH = handle_request

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    client = Client("https://unused.invalid", "PRIVATE_TOKEN_do_not_publish", timeout=10)
    # Only this local fixture uses HTTP. The production constructor requires HTTPS.
    client.base_url = "http://127.0.0.1:" + str(httpd.server_port)
    try:
        yield client, calls
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


class HelloTests(unittest.TestCase):
    def test_success_and_wire_contract(self):
        with server() as (client, calls):
            report = run(client, "journey-1", "deepseek-v4.1-flash")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["cleanup"], "archived")
        rpc = [body for _, path, body, _ in calls if path == "/agents/a2a"]
        self.assertEqual([r["method"] for r in rpc], ["message/send", "tasks/get"])
        self.assertEqual(rpc[0]["params"]["model"], "deepseek-v4.1-flash")
        self.assertEqual(rpc[0]["agent_workspace_id"], "system")
        self.assertEqual(calls[-1][2], {"status": "archived"})
        self.assertTrue(all(headers["X-Api-Key"] == "PRIVATE_TOKEN_do_not_publish"
                            for _, _, _, headers in calls))
        self.assertNotIn("PRIVATE_", json.dumps(report))

    def test_polls_without_resending(self):
        with server("working") as (client, calls):
            report = run(client, "journey-1", "model")
        self.assertEqual(report["status"], "passed")
        methods = [b["method"] for _, p, b, _ in calls if p == "/agents/a2a"]
        self.assertEqual(methods, ["message/send", "tasks/get", "tasks/get"])

    def test_failures_stay_red_and_do_not_disclose_data(self):
        for mode, expected in [("unauthorized", "http_error"), ("redirect", "http_error"),
                               ("failed", "task_not_completed"), ("wrong_reply", "reply_mismatch"),
                               ("wrong_context", "task_identity_mismatch"),
                               ("rpc_error", "rpc_error"), ("wrong_rpc_id", "invalid_rpc_response")]:
            with self.subTest(mode=mode), server(mode) as (client, calls):
                report = run(client, "journey-1", "model")
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["error"], expected)
                self.assertNotIn("PRIVATE_", json.dumps(report))
                self.assertFalse(any("credential-leak-target" in p for _, p, _, _ in calls))
                if mode in {"rpc_error", "wrong_rpc_id"}:
                    self.assertEqual(report["cleanup_error"], "task_outcome_unknown")
                    self.assertFalse(any(method == "PATCH" for method, _, _, _ in calls))

    def test_deadline_cancels_known_task_without_retrying_prompt(self):
        with server("never_complete") as (client, calls):
            client.deadline = time.monotonic() + 0.9
            report = run(client, "journey-1", "model")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error"], "deadline_exceeded")
        self.assertEqual(report["cleanup"], "archived")
        methods = [b["method"] for _, p, b, _ in calls if p == "/agents/a2a"]
        self.assertEqual(methods, ["message/send", "tasks/get", "tasks/cancel"])

    def test_input_required_is_failed_and_canceled_before_archive(self):
        with server("input") as (client, calls):
            report = run(client, "journey-1", "model")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["cleanup"], "archived")
        self.assertEqual(calls[-2][2]["method"], "tasks/cancel")

    def test_cleanup_failure_cannot_pass(self):
        with server("archive_failure") as (client, _):
            report = run(client, "journey-1", "model")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["cleanup_error"], "http_error")

    def test_expired_deadline_sends_nothing(self):
        with server() as (client, calls):
            client.deadline = time.monotonic() - 1
            report = run(client, "journey-1", "model")
            self.assertEqual(report["error"], "deadline_exceeded")
            self.assertEqual(calls, [])

    def test_user_echo_or_data_part_cannot_pass(self):
        for role, part in [("user", {"kind": "text", "text": "marker"}),
                           ("agent", {"kind": "data", "data": {"text": "marker"}})]:
            with self.subTest(role=role), self.assertRaises(Failure):
                assert_reply({"status": {"state": "completed", "message": {
                    "role": role, "parts": [part]}}}, "marker")

    def test_https_and_single_credential_configuration(self):
        for url, token in [("http://example.com", "key"), ("https://u:p@example.com", "key"),
                           ("https://example.com?x=1", "key"), ("https://example.com", "bad\nkey")]:
            with self.subTest(url=url), self.assertRaises(Failure):
                Client(url, token)


if __name__ == "__main__":
    unittest.main()
