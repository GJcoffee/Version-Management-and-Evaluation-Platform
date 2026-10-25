"""测试用的"原始" agent HTTP 服务：可以返回任意（包括不合法的）响应，并记录收到的请求。

用法::

    with RawAgentServer(lambda h, body: json_response(h, {"output": {...}})) as srv:
        base_url = srv.url        # srv.requests / srv.cancels 记录请求
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def json_response(
    h: BaseHTTPRequestHandler, obj: Any, status: int = 200, ctype: str = "application/json"
) -> None:
    body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def ndjson_response(h: BaseHTTPRequestHandler, lines: list[Any], delay: float = 0.0) -> None:
    """流式写出 NDJSON：元素为 dict（序列化为一行）或 str（原样写出一行）。"""
    h.send_response(200)
    h.send_header("Content-Type", "application/x-ndjson")
    h.send_header("Connection", "close")
    h.end_headers()
    h.close_connection = True
    for line in lines:
        text = line if isinstance(line, str) else json.dumps(line, ensure_ascii=False)
        h.wfile.write((text + "\n").encode("utf-8"))
        h.wfile.flush()
        if delay:
            time.sleep(delay)


class RawAgentServer:
    """线程里的 HTTP 服务；``on_run(handler, body)`` 负责 ``/evalp/run`` 的响应。"""

    def __init__(self, on_run: Callable[[BaseHTTPRequestHandler, dict], None], health: Any = None):
        self.on_run = on_run
        self.health = health if health is not None else {"ok": True, "protocol": "evalp-agent/1"}
        self.requests: list[dict[str, Any]] = []
        self.cancels: list[dict[str, Any]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def _read(self) -> dict:
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/evalp/health":
                    status, body = (
                        (200, owner.health) if not isinstance(owner.health, int) else (owner.health, {})
                    )
                    json_response(self, body, status)
                else:
                    json_response(self, {}, 404)

            def do_POST(self) -> None:  # noqa: N802
                body = self._read()
                if self.path == "/evalp/run":
                    owner.requests.append({"headers": dict(self.headers), "body": body})
                    try:
                        owner.on_run(self, body)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                elif self.path == "/evalp/cancel":
                    owner.cancels.append(body)
                    json_response(self, {"ok": True})
                else:
                    json_response(self, {}, 404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self) -> RawAgentServer:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
