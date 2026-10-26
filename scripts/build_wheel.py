#!/usr/bin/env python
"""构建 EvalP 的 wheel（内置前端产物与 Alembic 迁移），并检查 wheel 内容（详设 12 / 17 W10）。

步骤：
1. 构建前端（``npm ci`` + ``npm run build``，产物输出到 ``backend/evalp/static``）；
2. ``python -m build --wheel backend``；
3. 检查 wheel：必须包含前端 ``index.html`` 与静态资源、全部 Alembic 迁移
   （env.py / script.py.mako / versions/*.py）和 ``evalp`` 命令入口。检查不通过时退出码为 1。

用法::

    pip install build
    python scripts/build_wheel.py                    # 完整流程，wheel 输出到 dist/
    python scripts/build_wheel.py --skip-frontend    # 复用已有的 backend/evalp/static
    python scripts/build_wheel.py --outdir /tmp/out
    python scripts/build_wheel.py --check dist/evalp-0.1.0-py3-none-any.whl   # 只检查已有 wheel

只依赖标准库（以及构建时的 ``build`` 包）。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
PKG = BACKEND / "evalp"


def expected_migrations(pkg_dir: Path = PKG) -> list[str]:
    """源码树中应打进 wheel 的迁移文件（wheel 内路径）。"""
    base = pkg_dir / "store" / "migrations"
    files = ["evalp/store/migrations/env.py", "evalp/store/migrations/script.py.mako"]
    files += sorted(f"evalp/store/migrations/versions/{p.name}" for p in (base / "versions").glob("*.py"))
    return files


def check_wheel(wheel: Path, pkg_dir: Path = PKG) -> list[str]:
    """检查 wheel 内容，返回问题列表（空表示通过）。"""
    problems: list[str] = []
    with zipfile.ZipFile(wheel) as z:
        names = set(z.namelist())
    if "evalp/static/index.html" not in names:
        problems.append("缺少前端入口 evalp/static/index.html（先构建前端）")
    if not any(n.startswith("evalp/static/assets/") and n.endswith(".js") for n in names):
        problems.append("缺少前端静态资源 evalp/static/assets/*.js")
    for rel in expected_migrations(pkg_dir):
        if rel not in names:
            problems.append(f"缺少迁移文件 {rel}")
    if not any(n.startswith("evalp/store/migrations/versions/") and n.endswith(".py") for n in names):
        problems.append("wheel 中没有任何 Alembic 版本脚本")
    for required in ("evalp/__init__.py", "evalp/cli/main.py", "evalp/app.py"):
        if required not in names:
            problems.append(f"缺少 {required}")
    entry = [n for n in names if n.endswith(".dist-info/entry_points.txt")]
    if not entry:
        problems.append("缺少 entry_points.txt（evalp 命令）")
    else:
        with zipfile.ZipFile(wheel) as z:
            text = z.read(entry[0]).decode("utf-8")
        if "evalp = evalp.cli.main:app" not in text:
            problems.append("entry_points.txt 中没有 evalp = evalp.cli.main:app")
    # 不该进 wheel 的东西
    for n in sorted(names):
        if "__pycache__" in n or n.startswith(("tests/", "frontend/")) or n.endswith(".pyc"):
            problems.append(f"不应打进 wheel：{n}")
            break
    return problems


def _run(cmd: list[str], cwd: Path) -> None:
    print(f"$ {' '.join(cmd)}   (cwd={cwd})", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def build_frontend(*, install: bool) -> None:
    """构建前端到 backend/evalp/static。"""
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("找不到 npm：请安装 Node.js ≥ 20，或用 --skip-frontend 复用已有静态产物")
    if install or not (FRONTEND / "node_modules").is_dir():
        _run([npm, "ci"], FRONTEND)
    _run([npm, "run", "build"], FRONTEND)


def build_wheel(outdir: Path) -> Path:
    """调用 python -m build 构建 wheel，返回 wheel 路径。"""
    outdir.mkdir(parents=True, exist_ok=True)
    before = set(outdir.glob("evalp-*.whl"))
    try:
        _run([sys.executable, "-m", "build", "--wheel", "--outdir", str(outdir), str(BACKEND)], ROOT)
    finally:
        # setuptools 在源码目录里留下的中间产物（均已在 .gitignore 中）
        shutil.rmtree(BACKEND / "build", ignore_errors=True)
    wheels = sorted(set(outdir.glob("evalp-*.whl")) - before) or sorted(outdir.glob("evalp-*.whl"))
    if not wheels:
        raise SystemExit(f"{outdir} 下没有生成 wheel")
    return max(wheels, key=lambda p: p.stat().st_mtime)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="构建并检查 EvalP wheel")
    ap.add_argument("--outdir", type=Path, default=ROOT / "dist", help="wheel 输出目录（默认 dist/）")
    ap.add_argument("--skip-frontend", action="store_true", help="不构建前端，复用已有的静态产物")
    ap.add_argument("--npm-ci", action="store_true", help="即使 node_modules 已存在也重新执行 npm ci")
    ap.add_argument("--check", type=Path, metavar="WHEEL", help="只检查指定 wheel 的内容，不构建")
    args = ap.parse_args(argv)

    if args.check:
        wheel = args.check
    else:
        if not args.skip_frontend:
            build_frontend(install=args.npm_ci)
        if not (PKG / "static" / "index.html").is_file():
            raise SystemExit("backend/evalp/static/index.html 不存在：请先构建前端（去掉 --skip-frontend）")
        wheel = build_wheel(args.outdir)

    problems = check_wheel(wheel)
    if problems:
        print(f"wheel 检查未通过：{wheel}", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    with zipfile.ZipFile(wheel) as z:
        n_static = sum(1 for n in z.namelist() if n.startswith("evalp/static/"))
        n_mig = sum(
            1 for n in z.namelist() if n.startswith("evalp/store/migrations/versions/") and n.endswith(".py")
        )
    print(f"OK  {wheel}  ({wheel.stat().st_size / 1e6:.1f} MB；静态文件 {n_static} 个，迁移脚本 {n_mig} 个)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
