"""CLI 端到端：真实的 ``evalp serve`` 子进程 + CliRunner（init 到 pull 的完整往返）。"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from evalp.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(tmp_path: Path):
    data = tmp_path / "data"
    port = free_port()
    log = (tmp_path / "serve.log").open("w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "evalp", "serve", "--port", str(port), "--data-dir", str(data)],
        stdout=log, stderr=log,
    )  # fmt: skip
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"evalp serve 退出了: {(tmp_path / 'serve.log').read_text()[-2000:]}")
            try:
                httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            pytest.fail("evalp serve 启动超时")
        token = (data / "token").read_text().strip()
        r = httpx.post(
            f"http://127.0.0.1:{port}/api/v1/providers",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "deepseek",
                "base_url": "http://127.0.0.1:9/v1",
                "models": [{"name": "deepseek-chat"}],
            },
        )
        assert r.status_code == 201, r.text
        yield port, data
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def test_cli_roundtrip_against_real_server(live_server, tmp_path, monkeypatch):
    port, data = live_server
    proj = tmp_path / "bot"
    proj.mkdir()
    monkeypatch.chdir(proj)
    monkeypatch.setenv("EVALP_DATA_DIR", str(data))
    monkeypatch.setenv("COLUMNS", "200")

    def cli(*args: str):
        r = runner.invoke(app, list(args))
        assert r.exit_code == 0, f"{args}: {r.output}"
        return r.output

    cli("init", "--name", "e2e-bot")
    toml = (proj / ".evalp.toml").read_text(encoding="utf-8")
    (proj / ".evalp.toml").write_text(toml.replace("7860", str(port)), encoding="utf-8")

    out = cli("push")
    assert "已创建 agent 并推送" in out and "草稿校验: 通过" in out
    assert "没有变化" in cli("push")

    out = cli("publish", "-m", "初版", "--tag", "baseline")
    assert "已发布版本 v1" in out
    assert "版本已存在" in cli("publish")

    (proj / "prompts" / "system.md").write_text("你是一个简短的客服。\n", encoding="utf-8")
    assert "已发布版本 v2" in cli("publish", "-m", "改 prompt")

    out = cli("versions")
    assert "v2" in out and "baseline" in out and "改 prompt" in out
    out = cli("diff", "baseline", "v2")
    assert "prompts.system" in out and "+你是一个简短的客服。" in out

    # pull：删掉本地 prompt，再还原
    (proj / "prompts" / "system.md").unlink()
    out = cli("pull")
    assert (proj / "prompts" / "system.md").read_text(encoding="utf-8") == "你是一个简短的客服。\n"
    assert "file:prompts/system.md" in (proj / "agent.yaml").read_text(encoding="utf-8")


def test_new_commands_against_real_server(live_server, fake_openai_server, tmp_path, monkeypatch):
    """asset / dataset import / gc / eval status / eval gate / adapter check 对真实服务的往返。"""
    import json

    port, data = live_server
    headers = {"Authorization": f"Bearer {(data / 'token').read_text().strip()}"}
    base = f"http://127.0.0.1:{port}/api/v1"
    prov = httpx.get(f"{base}/providers", headers=headers).json()["items"][0]
    r = httpx.patch(
        f"{base}/providers/{prov['id']}", headers=headers, json={"base_url": str(fake_openai_server)}
    )
    assert r.status_code == 200, r.text  # 让评测 / 自检打到 Fake OpenAI
    proj = tmp_path / "bot2"
    proj.mkdir()
    monkeypatch.chdir(proj)
    monkeypatch.setenv("EVALP_DATA_DIR", str(data))
    monkeypatch.setenv("COLUMNS", "200")

    def cli(*args: str, code: int = 0):
        r = runner.invoke(app, list(args))
        assert r.exit_code == code, f"{args}: {r.output}"
        return r

    cli("init", "--name", "e2e-bot2")
    toml = (proj / ".evalp.toml").read_text(encoding="utf-8")
    (proj / ".evalp.toml").write_text(toml.replace("7860", str(port)), encoding="utf-8")

    # asset：新建 -> 内容相同 -> 新修订 -> 列表 / 查看 / diff
    (proj / "note.md").write_text("第一版\n", encoding="utf-8")
    assert "已创建" in cli("asset", "add", "prompt/note", "note.md", "-m", "初版").output
    assert "内容未变化" in cli("asset", "add", "prompt/note", "note.md").output
    (proj / "note.md").write_text("第二版\n", encoding="utf-8")
    assert "已新增修订" in cli("asset", "add", "prompt/note", "note.md").output
    assert "prompt/note" in cli("asset", "ls", "--kind", "prompt").output
    assert cli("asset", "show", "prompt/note").stdout == "第二版\n"
    assert cli("asset", "show", "prompt/note", "--rev", "1").stdout == "第一版\n"
    out = cli("asset", "diff", "prompt/note").output
    assert "-第一版" in out and "+第二版" in out
    cli("asset", "show", "prompt/none", code=1)

    # dataset import（JSONL 与带列映射的 CSV）
    out = cli("dataset", "import", "datasets/regression.jsonl", "--name", "reg").output
    assert "已创建数据集" in out and "3 个用例" in out
    (proj / "qa.csv").write_text("问题,答案\n你好,您好\n", encoding="utf-8")
    cli("dataset", "import", "qa.csv", "--name", "qa", code=1)  # 没有列映射
    assert (
        "1 个用例"
        in cli("dataset", "import", "qa.csv", "--name", "qa", "--map", "input=问题,expected=答案").output
    )
    assert "dataset/qa" in cli("asset", "ls", "--kind", "dataset").output

    assert "清理完成" in cli("gc").output

    # 评测（模型是 Fake OpenAI）：状态与门禁命令要能给出明确的结论
    cli("push")
    cli("publish", "-m", "初版", "--tag", "baseline")
    r = cli("eval", "run", "--wait", "--poll-interval", "0.2")  # 数据集和评估器取自 [eval]
    assert "已发起评测" in r.output
    run_id = next(w for w in r.output.split() if w.startswith("evr_"))
    status = cli("eval", "status", run_id).output
    assert run_id in status and "进度" in status
    gate = cli("eval", "gate", run_id, "--min-pass-rate", "0.5", "--json", code=1)
    result = json.loads(gate.stdout)
    assert result["passed"] is False and result["summary"]["run_id"] == run_id and result["reasons"]
    cli(
        "eval", "gate", "--run", run_id, "--min-pass-rate", "0.99", "--wait", "--poll-interval", "0.2", code=1
    )
    # 没有设置任何条件（无基线、无最低通过率）时，已成功结束的评测视为通过
    out = cli("eval", "gate", run_id).output
    assert "门禁通过" in out and "通过率不参与判定" in out

    # 适配器自检（Fake OpenAI 会正常回复）
    r = cli("adapter", "check")
    assert "适配器自检：通过" in r.output and "单轮" in r.output
