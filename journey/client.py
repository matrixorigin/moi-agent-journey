"""MOI HTTP transport. Errors deliberately exclude remote bodies and credentials."""

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class Failure(Exception):
    def __init__(self, code, http_status=None):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def segment(value):
    if not isinstance(value, str) or not value or value in (".", ".."):
        raise Failure("invalid_response")
    return urllib.parse.quote(value, safe="")


class Client:
    def __init__(self, base_url, pat, timeout=180):
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or not pat or any(c.isspace() for c in pat)):
            raise Failure("invalid_configuration")
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-API-Key": pat, "Accept": "application/json"}
        self.deadline = time.monotonic() + timeout
        self.opener = urllib.request.build_opener(NoRedirect())
        self.requests = 0

    def request(self, method, path, body=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Failure("deadline_exceeded")
        headers = dict(self.headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=data,
                                     headers=headers, method=method)
        self.requests += 1
        try:
            with self.opener.open(req, timeout=remaining) as response:
                raw = response.read(2_000_001)
            if time.monotonic() > self.deadline:
                raise Failure("deadline_exceeded")
            if len(raw) > 2_000_000:
                raise Failure("response_too_large")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise Failure("invalid_response")
            return result
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise Failure("http_error", status) from None
        except (TimeoutError, socket.timeout):
            raise Failure("deadline_exceeded") from None
        except urllib.error.URLError:
            raise Failure("transport_error") from None
        except OSError:
            raise Failure("transport_error") from None
        except (ValueError, UnicodeError):
            raise Failure("invalid_response") from None

    def product(self, method, path, body=None, *, expected_code=0):
        result = self.request(method, path, body)
        if result.get("code") != expected_code or not isinstance(result.get("data"), dict):
            raise Failure("product_error")
        return result["data"]

    def rpc(self, selector, method, params):
        request_id = str(uuid.uuid4())
        result = self.request("POST", "/agents/a2a", {
            "jsonrpc": "2.0", "id": request_id, **selector,
            "method": method, "params": params,
        })
        if result.get("jsonrpc") != "2.0" or result.get("id") != request_id:
            raise Failure("invalid_rpc_response")
        if "error" in result:
            raise Failure("rpc_error")
        if not isinstance(result.get("result"), dict):
            raise Failure("invalid_rpc_response")
        return result["result"]
