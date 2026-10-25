"""通用子进程宿主：在独立进程中调用 ``module:function``（Python 工具、Python 评估器共用）。

协议：

- 命令行 ``python -m evalp.toolhost --entry mod:func [--workdir DIR]``；
- stdin 读取一个 JSON 值（通常是 dict）作为函数的唯一参数，空输入视为 ``{}``；
- stdout 只写一行 JSON：``{"ok": true, "result": ...}`` 或
  ``{"ok": false, "error": {"type", "message", "traceback"}}``；
- 被调用代码里的 print / 子进程输出（文件描述符 1）一律重定向到 stderr，不会污染协议；
- 同步函数与 async 函数都支持；进程退出码总是 0（协议层错误用 ok=false 表示），宿主自身崩溃才非 0。

本模块只依赖标准库，因此可以被用户指定的任意 Python 解释器通过 ``-m`` 运行。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import os
import sys
import traceback
from typing import Any


def _load_entry(entry: str) -> Any:
    mod_name, sep, fn_name = entry.partition(":")
    if not sep or not mod_name or not fn_name:
        raise ValueError(f"entry 必须是 'module:function' 格式: {entry!r}")
    module = importlib.import_module(mod_name)
    obj: Any = module
    for part in fn_name.split("."):
        obj = getattr(obj, part)
    if not callable(obj):
        raise TypeError(f"{entry} 不是可调用对象")
    return obj


def _invoke(func: Any, args: Any) -> Any:
    result = func(args)
    if inspect.isawaitable(result):

        async def _await() -> Any:
            return await result

        result = asyncio.run(_await())
    return result


def _error(exc: BaseException) -> dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    }


def run(entry: str, workdir: str | None, raw_input: bytes) -> str:
    """执行一次调用，返回协议响应的 JSON 文本（单行，不抛异常）。"""
    try:
        if workdir:
            workdir = os.path.abspath(workdir)
            if workdir not in sys.path:
                sys.path.insert(0, workdir)
        elif os.getcwd() not in sys.path:
            sys.path.insert(0, os.getcwd())
        text = raw_input.decode("utf-8").strip()
        args = json.loads(text) if text else {}
        func = _load_entry(entry)
        result = _invoke(func, args)
        # 提前序列化以便不可序列化的返回值被当作错误上报，而不是写出半截 JSON
        payload = json.dumps({"ok": True, "result": result}, ensure_ascii=False, default=str)
        return payload
    except Exception as e:  # 宿主必须把一切异常转为协议响应
        return json.dumps({"ok": False, "error": _error(e)}, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evalp.toolhost", description="EvalP 子进程宿主")
    parser.add_argument("--entry", required=True, help="module:function")
    parser.add_argument("--workdir", default=None, help="加入 sys.path 的工作目录")
    ns = parser.parse_args(argv)

    # 保存真正的 stdout（fd 1）供协议使用，再把 fd 1 与 sys.stdout 都指向 stderr
    proto_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    raw = sys.stdin.buffer.read()

    line = run(ns.entry, ns.workdir, raw)
    with os.fdopen(proto_fd, "wb") as out:
        out.write(line.encode("utf-8") + b"\n")
        out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
