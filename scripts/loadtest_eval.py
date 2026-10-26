#!/usr/bin/env python
"""评测压测（详设 15.1 性能）：N 条用例 × 并发 C，Fake 模型固定延迟，度量吞吐与平台开销。

做法：用临时数据目录以子进程启动真实的 ``evalp serve``（数据库、调度器、Runner、事件落库都是真实路径），
本进程里用 uvicorn 线程启动 Fake OpenAI（tests/fixtures/fake_openai.py，不调用真实模型），
通过 REST 创建 Provider / agent / 数据集 / 评测并轮询到结束，同时每秒采样服务进程的 CPU 与内存。

用法::

    python scripts/loadtest_eval.py                       # 2000 用例 × 并发 64，模型延迟 200ms
    python scripts/loadtest_eval.py --cases 10000         # 详设里的完整规模
    python scripts/loadtest_eval.py --json result.json    # 同时写出 JSON 结果

压测前提：有效并发 = min(评测并发, scheduler.workers, Provider.max_concurrency)，所以脚本把后两者
都设为 ``--concurrency``（默认配置分别是 32 和 8）。

需要 ``pip install -e "backend[dev]"``（psutil、httpx 已在依赖中）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API = "/api/v1"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve_cmd(port: int, profile: Path | None) -> list[str]:
    """启动 evalp serve 的命令；指定 profile 时用 cProfile 包住主线程（事件循环），退出时写出统计文件。"""
    if profile is None:
        return [sys.executable, "-m", "evalp", "serve", "--port", str(port)]
    code = (
        "import cProfile, sys\n"
        "from evalp.cli.main import app\n"
        "pr = cProfile.Profile()\n"
        "pr.enable()\n"
        "try:\n"
        f"    app(['serve', '--port', '{port}'], standalone_mode=False)\n"
        "finally:\n"
        "    pr.disable()\n"
        f"    pr.dump_stats({str(profile)!r})\n"
    )
    return [sys.executable, "-c", code]


def start_fake(latency: float, chunk_size: int):
    """在线程里启动 Fake OpenAI，返回 (fake, server, root_url)。"""
    import uvicorn

    from tests.fixtures.fake_openai import FakeOpenAI

    fake = FakeOpenAI(latency=latency, chunk_size=chunk_size)
    server = uvicorn.Server(
        uvicorn.Config(fake.app, host="127.0.0.1", port=0, log_level="critical", lifespan="off")
    )
    threading.Thread(target=server.run, name="fake-openai", daemon=True).start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("Fake OpenAI 启动失败")
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    return fake, server, f"http://127.0.0.1:{port}"


class Sampler(threading.Thread):
    """每秒采样服务进程（含子进程）的 CPU 与 RSS。"""

    def __init__(self, pid: int) -> None:
        super().__init__(daemon=True)
        self.proc = psutil.Process(pid)
        self.cpu: list[float] = []
        self.rss: list[int] = []
        self._stop = threading.Event()

    def run(self) -> None:
        self.proc.cpu_percent(None)
        while not self._stop.wait(1.0):
            try:
                self.cpu.append(self.proc.cpu_percent(None))
                self.rss.append(self.proc.memory_info().rss)
            except psutil.Error:
                return

    def stop(self) -> None:
        self._stop.set()


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(math.ceil(q * len(xs))) - 1)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EvalP 评测压测")
    ap.add_argument("--cases", type=int, default=2000, help="用例数（默认 2000；详设规模为 10000）")
    ap.add_argument("--concurrency", type=int, default=64, help="评测并发（默认 64）")
    ap.add_argument("--latency", type=float, default=0.2, help="Fake 模型每次调用的延迟，秒（默认 0.2）")
    ap.add_argument("--workers", type=int, help="scheduler.workers（缺省等于 --concurrency）")
    ap.add_argument(
        "--chunk-size", type=int, default=4, help="流式回复每个增量的字符数（默认 4，越小增量越多）"
    )
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=1800, help="整体超时，秒")
    ap.add_argument("--json", type=Path, help="把结果写到 JSON 文件")
    ap.add_argument("--profile", type=Path, help="用 cProfile 运行服务进程（主线程），退出时写出 .prof 文件")
    ap.add_argument("--keep", action="store_true", help="保留临时数据目录（用于排查）")
    args = ap.parse_args(argv)

    data_dir = Path(tempfile.mkdtemp(prefix="evalp-loadtest-"))
    fake, fake_server, fake_root = start_fake(args.latency, args.chunk_size)
    port = free_port()
    env = {
        **os.environ,
        "EVALP_DATA_DIR": str(data_dir),
        "EVALP_SCHEDULER_WORKERS": str(args.workers or args.concurrency),
        "PYTHONUNBUFFERED": "1",
    }
    log = open(data_dir / "serve.log", "wb")  # noqa: SIM115 - 与子进程同生命周期
    server = subprocess.Popen(
        serve_cmd(port, args.profile),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    result: dict = {}
    sampler: Sampler | None = None
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 60
        while True:
            # 健康检查在 /api/v1 下且需要 token（/health 会被 SPA 回退成 index.html，没构建前端时是 404）
            token_file = data_dir / "token"
            try:
                if token_file.exists():
                    headers = {"Authorization": f"Bearer {token_file.read_text().strip()}"}
                    if httpx.get(f"{base}/api/v1/health", headers=headers, timeout=2).status_code == 200:
                        break
            except httpx.HTTPError:
                pass
            if server.poll() is not None or time.monotonic() > deadline:
                raise SystemExit(f"evalp serve 启动失败，日志：{data_dir / 'serve.log'}")
            time.sleep(0.3)
        token = (data_dir / "token").read_text().strip()
        c = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=120)

        def ok(r: httpx.Response, code: int | tuple[int, ...] = (200, 201)) -> dict:
            codes = (code,) if isinstance(code, int) else code
            if r.status_code not in codes:
                raise SystemExit(f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text[:500]}")
            return r.json()

        ok(
            c.post(
                f"{API}/providers",
                json={
                    "name": "fake",
                    "base_url": f"{fake_root}/v1",
                    "api_key": "sk-fake-key",
                    "models": [{"name": "fake-model", "context_window": 8192, "price_in": 2, "price_out": 6}],
                    "max_concurrency": args.concurrency,
                },
            )
        )
        agent = ok(
            c.post(
                f"{API}/agents",
                json={
                    "name": "loadtest",
                    "kind": "config",
                    "spec": {
                        "kind": "config",
                        "model": {"provider": "fake", "name": "fake-model"},
                        "prompts": {"system": "你是压测用的客服助手。"},
                    },
                },
            )
        )
        draft = agent["draft"]
        ok(c.post(f"{API}/drafts/{draft['id']}/publish", json={"expected_revision": draft["revision"]}))

        # 每条用例的输入都不同，避免命中结果缓存
        content = "\n".join(
            json.dumps(
                {"case_key": f"c{i}", "input": f"压测问题 {i}，请简单回答", "expected": "echo"},
                ensure_ascii=False,
            )
            for i in range(args.cases)
        )
        t = time.perf_counter()
        ok(c.post(f"{API}/datasets/import", json={"name": "load", "format": "jsonl", "content": content}))
        t_import = time.perf_counter() - t

        sampler = Sampler(server.pid)
        sampler.start()
        t_start = time.perf_counter()
        created = ok(
            c.post(
                f"{API}/evals",
                json={
                    "version_ids": ["loadtest:latest"],
                    "dataset": "asset://dataset/load@latest",
                    "evaluators": [{"key": "kw", "type": "contains"}],
                    "repeats": args.repeats,
                    "concurrency": args.concurrency,
                    "via": "api",
                },
            )
        )
        run_id = created["runs"][0]["id"]
        t_create = time.perf_counter() - t_start
        total = args.cases * args.repeats
        samples: list[tuple[float, int]] = []
        status = "queued"
        end_deadline = time.monotonic() + args.timeout
        while time.monotonic() < end_deadline:
            run = ok(c.get(f"{API}/evals/{run_id}"))
            status = run["status"]
            done = run["progress"].get("done", 0) + run["progress"].get("failed", 0)
            samples.append((time.perf_counter() - t_start, done))
            print(f"\r[{samples[-1][0]:6.1f}s] {status:<22} {done}/{total}", end="", flush=True)
            if status not in ("queued", "running"):
                break
            time.sleep(1.0)
        print()
        elapsed = time.perf_counter() - t_start
        sampler.stop()

        # 报告首屏：详情（含汇总）与第一页用例
        t = time.perf_counter()
        detail = ok(c.get(f"{API}/evals/{run_id}"))
        t_detail = time.perf_counter() - t
        t = time.perf_counter()
        ok(c.get(f"{API}/evals/{run_id}/items", params={"limit": 50}))
        t_items = time.perf_counter() - t

        ideal = math.ceil(total / args.concurrency) * args.latency
        db = data_dir / "evalp.db"
        db_size = sum(p.stat().st_size for p in data_dir.glob("evalp.db*"))
        # 稳态吞吐：取进度 10% ~ 90% 之间的斜率，排除启动与收尾
        lo = [s for s in samples if s[1] >= total * 0.1]
        hi = [s for s in samples if s[1] <= total * 0.9]
        steady = 0.0
        if lo and hi and hi[-1][0] > lo[0][0]:
            steady = (hi[-1][1] - lo[0][1]) / (hi[-1][0] - lo[0][0])
        result = {
            "cases": args.cases,
            "repeats": args.repeats,
            "concurrency": args.concurrency,
            "model_latency_s": args.latency,
            "stream_chunk_chars": args.chunk_size,
            "status": status,
            "stats": detail.get("stats"),
            "elapsed_s": round(elapsed, 2),
            "ideal_s": round(ideal, 2),
            "overhead_s": round(elapsed - ideal, 2),
            "overhead_per_call_ms": round((elapsed - ideal) / total * args.concurrency * 1000, 1),
            "throughput_items_per_s": round(total / elapsed, 1),
            "steady_throughput_items_per_s": round(steady, 1),
            "ideal_throughput_items_per_s": round(args.concurrency / args.latency, 1),
            "fake_max_inflight": fake.max_inflight,
            "fake_chat_requests": len(fake.chat_requests),
            "import_s": round(t_import, 2),
            "create_s": round(t_create, 2),
            "report_detail_ms": round(t_detail * 1000, 1),
            "report_items_page_ms": round(t_items * 1000, 1),
            "db_size_mb": round(db_size / 1e6, 1),
            "db_bytes_per_item": round(db_size / total),
            "server_cpu_avg_pct": round(statistics.mean(sampler.cpu), 1) if sampler.cpu else None,
            "server_cpu_p95_pct": round(pct(sampler.cpu, 0.95), 1) if sampler.cpu else None,
            "server_rss_peak_mb": round(max(sampler.rss) / 1e6, 1) if sampler.rss else None,
            "data_dir": str(data_dir) if args.keep else None,
            "db_exists": db.exists(),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.json:
            args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if status in ("completed", "completed_with_errors") else 1
    finally:
        if sampler:
            sampler.stop()
        # 只结束本脚本启动的服务进程（按 PID）
        if server.poll() is None:
            # uvicorn 收到 SIGTERM 后会重新抛出信号直接终止进程，
            # profile 模式改用 SIGINT 才能写出统计
            if args.profile and hasattr(signal, "SIGINT"):
                server.send_signal(signal.SIGINT)
            else:
                server.terminate()
            try:
                server.wait(60 if args.profile else 15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(5)
        log.close()
        fake_server.should_exit = True
        if not args.keep:
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
