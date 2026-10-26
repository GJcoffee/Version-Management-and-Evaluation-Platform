"""密钥存储（详设 16.1）。

优先使用系统钥匙串（keyring，服务名 ``evalp``，条目名如 ``provider:{id}``）；keyring 不可用或报错时，
回退到 Fernet 加密文件 ``{data_dir}/secrets.enc``（密钥由机器标识派生，文件权限 600）。

注意：模块名与标准库 ``secrets`` 相同，包内一律使用绝对导入 ``evalp.secrets``；生成 token 请
``import secrets``（标准库，位于顶层，不受影响）。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import platform
import subprocess
import threading
import uuid
from pathlib import Path

import keyring
from cryptography.fernet import Fernet, InvalidToken

from evalp.errors import InternalError
from evalp.logging import get_logger
from evalp.utils.fsperm import restrict_to_owner

log = get_logger(__name__)

SERVICE_NAME = "evalp"
_KDF_SALT = b"evalp/secrets/v1"
_KDF_ITERATIONS = 200_000


def provider_secret_key(provider_id: str) -> str:
    """Provider 密钥的条目名。"""
    return f"provider:{provider_id}"


def _machine_id() -> str:
    """读取机器标识；取不到时退化为 MAC 地址。"""
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            text = Path(p).read_text().strip()
            if text:
                return text
        except OSError:
            pass
    system = platform.system()
    try:
        if system == "Darwin":
            out = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True, timeout=5
            ).stdout
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split('"')[-2]
        elif system == "Windows":
            import winreg  # type: ignore[import-not-found]

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:  # type: ignore[attr-defined]
                return str(winreg.QueryValueEx(k, "MachineGuid")[0])  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    return f"mac-{uuid.getnode():012x}"


def derive_fernet_key(machine_id: str | None = None) -> bytes:
    """由机器标识（+ 用户名）派生 Fernet 密钥。"""
    material = f"{machine_id or _machine_id()}|{os.environ.get('USER') or os.environ.get('USERNAME') or ''}"
    raw = hashlib.pbkdf2_hmac("sha256", material.encode(), _KDF_SALT, _KDF_ITERATIONS, dklen=32)
    return base64.urlsafe_b64encode(raw)


class SecretStore:
    """keyring 优先、Fernet 文件回退的密钥存储。方法均为异步（keyring 调用在线程中执行）。"""

    def __init__(self, secrets_path: Path | str, *, machine_id: str | None = None) -> None:
        self.path = Path(secrets_path)
        self._machine_id = machine_id
        self._fernet_cache: Fernet | None = None
        self._file_lock = threading.Lock()

    @property
    def _fernet(self) -> Fernet:
        """首次使用时才派生密钥（PBKDF2 较慢）。"""
        if self._fernet_cache is None:
            self._fernet_cache = Fernet(derive_fernet_key(self._machine_id))
        return self._fernet_cache

    # ------------------------------------------------------------ 文件后端

    def _load_file(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        blob = self.path.read_bytes()
        if not blob:
            return {}
        try:
            return json.loads(self._fernet.decrypt(blob).decode("utf-8"))
        except (InvalidToken, ValueError) as e:
            raise InternalError(
                f"无法解密 {self.path}（可能换了机器或文件损坏），请删除该文件后重新设置密钥"
            ) from e

    def _save_file(self, data: dict[str, str]) -> None:
        token = self._fernet.encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(token)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        restrict_to_owner(self.path)

    def _file_set(self, key: str, value: str) -> None:
        with self._file_lock:
            data = self._load_file()
            data[key] = value
            self._save_file(data)

    def _file_get(self, key: str) -> str | None:
        with self._file_lock:
            return self._load_file().get(key)

    def _file_delete(self, key: str) -> bool:
        with self._file_lock:
            data = self._load_file()
            if key not in data:
                return False
            del data[key]
            self._save_file(data)
            return True

    # ------------------------------------------------------------ 同步实现

    def _set_sync(self, key: str, value: str) -> str:
        """返回实际使用的后端：keyring | file。"""
        try:
            keyring.set_password(SERVICE_NAME, key, value)
        except Exception as e:  # noqa: BLE001 - keyring 的任何失败都回退到文件
            log.warning("secrets.keyring_unavailable", error=type(e).__name__, fallback="file")
            self._file_set(key, value)
            return "file"
        # 之前可能存过文件副本，避免读到旧值
        self._file_delete(key)
        return "keyring"

    def _get_sync(self, key: str) -> str | None:
        try:
            value = keyring.get_password(SERVICE_NAME, key)
        except Exception:  # noqa: BLE001
            value = None
        if value is not None:
            return value
        return self._file_get(key)

    def _delete_sync(self, key: str) -> bool:
        deleted = False
        try:
            keyring.delete_password(SERVICE_NAME, key)
            deleted = True
        except Exception:  # noqa: BLE001 - 条目不存在或 keyring 不可用
            pass
        return self._file_delete(key) or deleted

    # ------------------------------------------------------------ 异步接口

    async def set(self, key: str, value: str) -> str:
        """保存密钥，返回所用后端（"keyring" 或 "file"）。"""
        return await asyncio.to_thread(self._set_sync, key, value)

    async def get(self, key: str) -> str | None:
        """读取密钥；不存在返回 None。"""
        return await asyncio.to_thread(self._get_sync, key)

    async def delete(self, key: str) -> bool:
        """删除密钥；返回是否真的删除了东西。"""
        return await asyncio.to_thread(self._delete_sync, key)
