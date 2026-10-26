"""压测脚本冒烟：小规模跑通 scripts/loadtest_eval.py（evalp serve 子进程 + Fake OpenAI）。"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_loadtest_script_small_run(tmp_path):
    out = tmp_path / "result.json"
    r = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "loadtest_eval.py"),
            "--cases",
            "12",
            "--concurrency",
            "4",
            "--latency",
            "0.05",
            "--timeout",
            "120",
            "--json",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=240,
        cwd=ROOT,
    )
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    res = json.loads(out.read_text(encoding="utf-8"))
    assert res["status"] == "completed"
    assert res["cases"] == 12 and res["concurrency"] == 4
    assert res["fake_chat_requests"] == 12  # 每条用例恰好一次模型调用
    assert res["fake_max_inflight"] <= 4  # 并发不超过设定
    assert res["throughput_items_per_s"] > 0
    assert res["stats"]["failed"] == 0
