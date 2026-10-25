"""HTTP 服务（标准库 ``ThreadingHTTPServer`` + 一个后台 asyncio 事件循环线程）。

- ``POST /evalp/run``：请求体为 ``evalp-agent/1`` 请求，响应为流式 NDJSON（``application/x-ndjson``，L2），
  每个事件产生后立即写出；响应以关闭连接结束（HTTP/1.0 语义）；
- ``GET /evalp/health``：``{"ok": true, "protocol": "evalp-agent/1"}``；
- ``POST /evalp/cancel``：``{"turn_id": …}``，取消对应的执行；平台断开连接也会取消。
"""

from __future__ import annotations

import asyncio
import json
import queue
import select
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

from .agent import PROTOCOL

if TYPE_CHECKING:
    from .agent import Agent

_DONE = object()


def _client_gone(sock: socket.socket) -> bool:
    """客户端是否已断开（连接上有可读数据且读到 EOF）。"""
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        if not readable:
            return False
        return sock.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


class HttpServerHandle:
    """运行中的 HTTP 服务句柄。"""

    def __init__(self, agent: Agent, host: str, port: int) -> None:
        self.agent = agent
        self._loop = asyncio.new_event_loop()
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._loop_thread = threading.Thread(target=self._run_loop, name="evalp-sdk-loop", daemon=True)
        handle = self

        class Handler(_Handler):
            owner = handle

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self._serve_thread: threading.Thread | None = None
        self._loop_thread.start()

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    @property
    def url(self) -> str:
        host = self.httpd.server_address[0]
        return f"http://{host}:{self.port}"

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def start(self) -> HttpServerHandle:
        """在后台线程开始处理请求。"""
        self._serve_thread = threading.Thread(
            target=self.httpd.serve_forever, name="evalp-sdk-http", daemon=True
        )
        self._serve_thread.start()
        return self

    def serve_forever(self) -> None:
        """在当前线程处理请求（阻塞），直到 close()。"""
        self.httpd.serve_forever()

    def close(self) -> None:
        """停止服务并取消所有进行中的执行。"""
        try:
            self.httpd.shutdown()
        finally:
            self.httpd.server_close()
            for task in list(self._tasks.values()):
                self._loop.call_soon_threadsafe(task.cancel)
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=5)
            if self._serve_thread is not None:
                self._serve_thread.join(timeout=5)

    def __enter__(self) -> HttpServerHandle:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- 在事件循环线程里执行
    def submit(self, request: dict[str, Any], out: queue.Queue[Any]):
        turn_id = str(request.get("turn_id") or id(out))

        async def job() -> None:
            self._tasks[turn_id] = asyncio.current_task()  # type: ignore[assignment]
            try:
                await self.agent.execute(request, out.put)
            except asyncio.CancelledError:
                pass
            finally:
                self._tasks.pop(turn_id, None)
                out.put(_DONE)

        return asyncio.run_coroutine_threadsafe(job(), self._loop)

    def cancel(self, turn_id: str) -> bool:
        task = self._tasks.get(turn_id)
        if task is None:
            return False
        self._loop.call_soon_threadsafe(task.cancel)
        return True


class _Handler(BaseHTTPRequestHandler):
    owner: HttpServerHandle
    server_version = "evalp-sdk"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # 保持静默；需要日志时由 agent 自己输出

    def _json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] == "/evalp/health":
            self._json(200, {"ok": True, "protocol": PROTOCOL, "agent": self.owner.agent.name})
        else:
            self._json(404, {"error": f"未知路径: {self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        try:
            body = self._body()
            if not isinstance(body, dict):
                raise ValueError("请求体必须是 JSON 对象")
        except (ValueError, UnicodeDecodeError) as e:
            self._json(400, {"error": f"请求体不是合法的 JSON 对象: {e}"})
            return
        if path == "/evalp/cancel":
            self._json(200, {"ok": True, "cancelled": self.owner.cancel(str(body.get("turn_id", "")))})
        elif path == "/evalp/run":
            self._run(body)
        else:
            self._json(404, {"error": f"未知路径: {self.path}"})

    def _run(self, body: dict[str, Any]) -> None:
        out: queue.Queue[Any] = queue.Queue()
        fut = self.owner.submit(body, out)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            while True:
                try:
                    item = out.get(timeout=0.2)
                except queue.Empty:
                    if _client_gone(self.connection):  # 平台断开：取消执行
                        fut.cancel()
                        return
                    continue
                if item is _DONE:
                    return
                self.wfile.write((json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            fut.cancel()


def start_http(agent: Agent, port: int = 0, host: str = "127.0.0.1") -> HttpServerHandle:
    """后台线程启动服务并返回句柄；``port=0`` 表示随机端口。"""
    return HttpServerHandle(agent, host, port).start()


def serve_http(agent: Agent, port: int = 8001, host: str = "127.0.0.1") -> None:
    """启动 HTTP 服务并阻塞，直到 Ctrl+C。"""
    handle = HttpServerHandle(agent, host, port)
    print(f"[evalp-sdk] {agent.name} 已启动: {handle.url}/evalp/run", file=sys.stderr, flush=True)
    try:
        handle.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        handle.close()
