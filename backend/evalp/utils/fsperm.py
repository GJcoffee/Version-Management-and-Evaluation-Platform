"""把敏感文件（API token、secrets.enc）限制为仅当前用户可读写（详设 16.1）。

- POSIX：``chmod 600``，行为与之前完全一致；
- Windows：``chmod`` 只能控制只读位，所以改用系统自带的 ``icacls``（subprocess，无新增依赖）：
  去掉继承的 ACL，并只授予当前用户权限。

任何失败都只记录警告、不抛异常（与此前 chmod 失败时静默忽略的行为一致，不能因此让服务起不来）。
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from pathlib import Path

from evalp.logging import get_logger

log = get_logger(__name__)

ICACLS_TIMEOUT_S = 10


def is_windows() -> bool:
    """是否运行在 Windows 上（独立成函数，便于测试里 mock）。"""
    return sys.platform == "win32"


def windows_current_user() -> str:
    """当前用户，形如 ``DOMAIN\\user``；取不到域时只返回用户名。"""
    name = os.environ.get("USERNAME") or getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    return f"{domain}\\{name}" if domain else name


def icacls_command(path: Path, user: str) -> list[str]:
    """构造 icacls 命令：移除继承的权限，仅授予 ``user`` 完全控制（独占，替换已有授权）。"""
    return ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(F)"]


def restrict_to_owner(path: Path) -> bool:
    """把文件权限收紧为仅当前用户可读写；成功返回 True，失败只记录警告并返回 False。"""
    path = Path(path)
    if not is_windows():
        try:
            os.chmod(path, 0o600)
        except OSError:
            return False
        return True
    cmd = icacls_command(path, windows_current_user())
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=ICACLS_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("fsperm.icacls_failed", path=str(path), error=str(e))
        return False
    if proc.returncode != 0:
        log.warning(
            "fsperm.icacls_failed",
            path=str(path),
            returncode=proc.returncode,
            output=(proc.stderr or proc.stdout or "").strip()[:200],
        )
        return False
    return True
