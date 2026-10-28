"""MOI HTTP transport. Errors deliberately exclude remote bodies and credentials."""

import json
import http.client
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class Failure(Exception):
    def __init__(self, code, http_status=None, details=None):
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.details = details


def error_record(stage, error, **context):
    """Keep structured failure information when reporting an execution error."""
    if isinstance(error, Failure):
        return {'stage': stage, 'reason': error.code, 'http_status': error.http_status,
                'details': error.details, **context}
    return {'stage': stage, 'reason': type(error).__name__ + ':' + str(error), **context}


def transport_details(error):
    """Record a bounded network failure category without remote error text."""
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, socket.gaierror):
        category = 'dns'
    elif isinstance(reason, ssl.SSLCertVerificationError):
        category = 'tls_certificate'
    elif isinstance(reason, ssl.SSLError):
        category = 'tls'
    elif isinstance(reason, ConnectionRefusedError):
        category = 'connection_refused'
    elif isinstance(reason, http.client.RemoteDisconnected):
        category = 'connection_closed'
    elif isinstance(reason, (ConnectionResetError, BrokenPipeError)):
        category = 'connection_reset'
    elif isinstance(reason, TimeoutError):
        category = 'timeout'
    elif isinstance(reason, http.client.HTTPException):
        category = 'http_protocol'
    else:
        category = 'network'
    details = {'category': category, 'exception': type(reason).__name__}
    error_number = getattr(reason, 'errno', None)
    if type(error_number) is int:
        details['errno'] = error_number
    return details


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def segment(value):
    if not isinstance(value, str) or not value or value in (".", ".."):
        raise Failure("invalid_response")
    return urllib.parse.quote(value, safe="")


class Client:
    def __init__(self, base_url, pat, timeout=180, observer=None):
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
        self.observer = observer

    def request(self, method, path, body=None, *, raw=None, content_type=None,
                binary=False, max_bytes=32_000_000):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Failure("deadline_exceeded")
        headers = dict(self.headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if raw is not None:
            data = raw
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.base_url + path, data=data,
                                     headers=headers, method=method)
        self.requests += 1
        started = time.monotonic()
        def observe(status, response, response_headers):
            if self.observer:
                self.observer({"method": method, "path": path, "request": body,
                               "status": status, "response": response,
                               "headers": {k: v for k, v in response_headers.items()
                                           if k.lower() in {"x-trace-id", "x-request-id", "traceparent"}},
                               "seconds": round(time.monotonic() - started, 3)})
        try:
            with self.opener.open(req, timeout=remaining) as response:
                response_headers = dict(response.headers)
                status = response.status
                payload = response.read(max_bytes + 1)
            if time.monotonic() > self.deadline:
                raise Failure("deadline_exceeded")
            if len(payload) > max_bytes:
                raise Failure("response_too_large")
            if binary:
                observe(status, {"bytes": len(payload)}, response_headers)
                return payload
            result = json.loads(payload)
            observe(status, result, response_headers)
            if not isinstance(result, dict):
                raise Failure("invalid_response")
            return result
        except urllib.error.HTTPError as error:
            status = error.code
            payload = error.read(65536).decode("utf-8", errors="replace")
            observe(status, {"error_body": payload}, dict(error.headers))
            error.close()
            raise Failure("http_error", status) from None
        except (TimeoutError, socket.timeout) as error:
            raise Failure("deadline_exceeded", details=transport_details(error)) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            raise Failure("transport_error", details=transport_details(error)) from None
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
            raise Failure("rpc_error", details=result["error"])
        if not isinstance(result.get("result"), dict):
            raise Failure("invalid_rpc_response")
        return result["result"]

    def stream_rpc(self, selector, params, *, method='message/stream'):
        """Read an Agent admission or task resubscription as an SSE stream."""
        if method not in {'message/stream', 'tasks/resubscribe'}:
            raise Failure('invalid_stream_method')
        request_id = str(uuid.uuid4())
        body = {"jsonrpc": "2.0", "id": request_id, **selector,
                "method": method, "params": params}
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Failure("deadline_exceeded")
        headers = {**self.headers, "Accept": "text/event-stream", "Content-Type": "application/json"}
        request = urllib.request.Request(self.base_url + "/agents/a2a", data=json.dumps(body).encode(),
                                         headers=headers, method="POST")
        self.requests += 1
        try:
            with self.opener.open(request, timeout=remaining) as response:
                if self.observer:
                    self.observer({"method": "POST", "path": "/agents/a2a", "request": body,
                        "status": response.status, "response": {"stream": True}, "headers": {}, "seconds": 0})
                if response.headers.get_content_type() != "text/event-stream":
                    if self.observer:
                        self.observer({"method":"POST","path":"/agents/a2a","status":response.status,
                            "response":{"error_body":response.read(65536).decode('utf-8',errors='replace')}})
                    raise Failure("invalid_stream_content_type")
                data, sequence = [], None
                while True:
                    if time.monotonic() >= self.deadline:
                        raise Failure("deadline_exceeded")
                    line = response.readline(2_000_001)
                    if not line:
                        if data:
                            raise Failure("incomplete_stream_frame")
                        return
                    if len(line) > 2_000_000:
                        raise Failure("stream_frame_too_large")
                    text = line.decode("utf-8").rstrip("\r\n")
                    if not text:
                        if not data:
                            continue
                        frame = json.loads("\n".join(data))
                        if frame.get("jsonrpc") != "2.0" or frame.get("id") != request_id:
                            raise Failure("invalid_rpc_response")
                        if "error" in frame:
                            if self.observer:
                                self.observer({"method":"POST","path":"/agents/a2a","status":response.status,"response":frame})
                            raise Failure("rpc_error",details=frame["error"])
                        if not isinstance(frame.get("result"), dict):
                            raise Failure("invalid_rpc_response")
                        yield {"sequence": sequence, "result": frame["result"]}
                        data, sequence = [], None
                    elif text.startswith("data:"):
                        data.append(text[5:].lstrip(" "))
                        if sum(len(part) for part in data) > 2_000_000:
                            raise Failure("stream_frame_too_large")
                    elif text.startswith("id:"):
                        sequence = text[3:].strip()
        except urllib.error.HTTPError as error:
            status = error.code
            if self.observer:
                self.observer({"method":"POST","path":"/agents/a2a","status":status,
                    "response":{"error_body":error.read(65536).decode('utf-8',errors='replace')}})
            error.close()
            raise Failure("http_error", status) from None
        except (TimeoutError, socket.timeout) as error:
            raise Failure("deadline_exceeded", details=transport_details(error)) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            raise Failure("transport_error", details=transport_details(error)) from None
        except (ValueError, UnicodeError):
            raise Failure("invalid_stream_response") from None

    def upload(self, root, path):
        """Use the same private-Catalog upload contract as the product UI."""
        import mimetypes
        boundary = "journey-" + uuid.uuid4().hex
        name = path.name
        if any(c in name for c in '\r\n"'):
            raise Failure("invalid_upload_name")
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        raw = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
               f'filename="{name}"\r\nContent-Type: {mime}\r\n\r\n').encode()
        raw += path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        result = self.request("POST", root + "/catalog-files/uploads", raw=raw,
                              content_type="multipart/form-data; boundary=" + boundary)
        if result.get("code") != "OK" or not isinstance(result.get("data"), dict):
            code = result.get('code')
            if not isinstance(code, (str, int)) or isinstance(code, bool) or len(str(code)) > 80:
                code = None
            raise Failure("upload_product_error", details={'product_code': code})
        return result["data"]
