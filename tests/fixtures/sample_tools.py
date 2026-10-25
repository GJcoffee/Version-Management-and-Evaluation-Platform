"""供 Python 工具 / toolhost 测试使用的示例函数（workdir 加入 sys.path 后以 ``sample_tools:xxx`` 引用）。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time


def echo(args):
    return {"echo": args}


def greet(args):
    return f"你好，{args['name']}"


async def async_double(args):
    await asyncio.sleep(0.01)
    return args["n"] * 2


def noisy(args):
    print("这行 print 不能污染 stdout 协议")
    os.write(1, "直接写 fd 1 也不行\n".encode())
    sys.stdout.write("another\n")
    print("to stderr", file=sys.stderr)
    return "done"


def boom(args):
    raise ValueError("出错了: " + str(args.get("why", "?")))


def not_serializable(args):
    return {"obj": object, "set": {1}}


def sleeper(args):
    time.sleep(args.get("seconds", 60))
    return "late"


def spawn_and_sleep(args):
    """起一个孙子进程，记录它的 pid，然后自己睡眠（用来验证整树被杀）。"""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(args["pidfile"], "w") as f:
        f.write(str(child.pid))
    time.sleep(60)


def cwd_and_path(args):
    return {"cwd": os.getcwd(), "has_workdir": any(p.endswith("fixtures") for p in sys.path)}


def read_env(args):
    return os.environ.get(args["name"])
