"""wheel 构建脚本：内容检查逻辑（合成 wheel）与打包配置；真实构建需显式开启（EVALP_TEST_WHEEL=1）。"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_wheel.py"
PKG = ROOT / "backend" / "evalp"


def _load():
    spec = importlib.util.spec_from_file_location("build_wheel_script", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bw = _load()


def _make_wheel(path: Path, *, drop: tuple[str, ...] = (), extra: tuple[str, ...] = ()) -> Path:
    names = {
        "evalp/__init__.py": "",
        "evalp/app.py": "",
        "evalp/cli/main.py": "",
        "evalp/static/index.html": "<html></html>",
        "evalp/static/assets/index-abc.js": "x",
        "evalp-0.1.0.dist-info/entry_points.txt": "[console_scripts]\nevalp = evalp.cli.main:app\n",
    }
    for rel in bw.expected_migrations():
        names[rel] = ""
    for d in drop:
        names.pop(d)
    for e in extra:
        names[e] = ""
    with zipfile.ZipFile(path, "w") as z:
        for n, c in names.items():
            z.writestr(n, c)
    return path


def test_expected_migrations_cover_source_tree():
    exp = bw.expected_migrations()
    assert "evalp/store/migrations/env.py" in exp
    assert "evalp/store/migrations/script.py.mako" in exp
    on_disk = {p.name for p in (PKG / "store" / "migrations" / "versions").glob("*.py")}
    assert on_disk and all(f"evalp/store/migrations/versions/{n}" in exp for n in on_disk)


def test_check_wheel_accepts_complete_wheel(tmp_path):
    assert bw.check_wheel(_make_wheel(tmp_path / "ok.whl")) == []


def test_check_wheel_reports_missing_frontend(tmp_path):
    w = _make_wheel(tmp_path / "w.whl", drop=("evalp/static/index.html", "evalp/static/assets/index-abc.js"))
    problems = bw.check_wheel(w)
    assert any("index.html" in p for p in problems)
    assert any("assets" in p for p in problems)


def test_check_wheel_reports_missing_migration(tmp_path):
    victim = next(m for m in bw.expected_migrations() if "versions/0002" in m)
    problems = bw.check_wheel(_make_wheel(tmp_path / "w.whl", drop=(victim,)))
    assert any(victim in p for p in problems)


def test_check_wheel_reports_missing_entry_point_and_junk(tmp_path):
    w = _make_wheel(
        tmp_path / "w.whl", drop=("evalp-0.1.0.dist-info/entry_points.txt",), extra=("tests/x.py",)
    )
    problems = bw.check_wheel(w)
    assert any("entry_points" in p for p in problems)
    assert any("不应打进 wheel" in p for p in problems)


def test_package_data_declares_static_and_migrations():
    """versions/ 不是 Python 包（没有 __init__.py），迁移脚本与前端产物只能靠 package-data 进入 wheel。"""
    cfg = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = cfg["tool"]["setuptools"]["package-data"]["evalp"]
    for required in ("static/**/*", "store/migrations/*.mako", "store/migrations/versions/*.py"):
        assert required in patterns
    assert not (PKG / "store" / "migrations" / "versions" / "__init__.py").exists()


@pytest.mark.skipif(
    os.environ.get("EVALP_TEST_WHEEL") != "1", reason="真实构建较慢且需要网络：设置 EVALP_TEST_WHEEL=1 开启"
)
def test_real_wheel_build(tmp_path):
    """真实构建（复用已有的 backend/evalp/static）并检查内容。"""
    if not (PKG / "static" / "index.html").is_file():
        pytest.skip("尚未构建前端")
    pytest.importorskip("build")
    out = tmp_path / "dist"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--skip-frontend", "--outdir", str(out)], capture_output=True, text=True
    )
    shutil.rmtree(ROOT / "backend" / "build", ignore_errors=True)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert list(out.glob("evalp-*.whl"))
