import os
import stat

import keyring
import pytest
from evalp.errors import InternalError
from evalp.secrets import SERVICE_NAME, SecretStore, provider_secret_key
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.data: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def get_password(self, service, username):
        return self.data.get((service, username))

    def delete_password(self, service, username):
        try:
            del self.data[(service, username)]
        except KeyError:
            raise PasswordDeleteError("not found") from None


@pytest.fixture
def mem_keyring():
    prev = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    yield kr
    keyring.set_keyring(prev)


async def test_file_fallback_roundtrip(tmp_path):
    # conftest 已把 keyring 强制设为 fail 后端
    store = SecretStore(tmp_path / "secrets.enc", machine_id="m1")
    key = provider_secret_key("prv_1")
    assert key == "provider:prv_1"
    assert await store.get(key) is None
    assert await store.set(key, "sk-密钥") == "file"
    assert await store.get(key) == "sk-密钥"
    # 密文中不含明文，权限 600
    raw = (tmp_path / "secrets.enc").read_bytes()
    assert b"sk-" not in raw
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / "secrets.enc").stat().st_mode) == 0o600
    # 另一个实例（同一机器标识）能读取
    assert await SecretStore(tmp_path / "secrets.enc", machine_id="m1").get(key) == "sk-密钥"
    assert await store.delete(key) is True
    assert await store.get(key) is None
    assert await store.delete(key) is False


async def test_wrong_machine_cannot_decrypt(tmp_path):
    await SecretStore(tmp_path / "s.enc", machine_id="m1").set("k", "v")
    with pytest.raises(InternalError):
        await SecretStore(tmp_path / "s.enc", machine_id="other").get("k")


async def test_uses_keyring_when_available(tmp_path, mem_keyring):
    store = SecretStore(tmp_path / "secrets.enc", machine_id="m")
    assert await store.set("provider:x", "v1") == "keyring"
    assert mem_keyring.data[(SERVICE_NAME, "provider:x")] == "v1"
    assert not (tmp_path / "secrets.enc").exists()
    assert await store.get("provider:x") == "v1"
    assert await store.delete("provider:x") is True
    assert await store.get("provider:x") is None


async def test_keyring_error_falls_back_to_file(tmp_path, mem_keyring, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("dbus down")

    monkeypatch.setattr(mem_keyring, "set_password", boom)
    store = SecretStore(tmp_path / "secrets.enc", machine_id="m")
    assert await store.set("k", "v") == "file"
    monkeypatch.setattr(mem_keyring, "get_password", boom)
    assert await store.get("k") == "v"


async def test_file_value_is_found_when_keyring_empty(tmp_path, mem_keyring):
    store = SecretStore(tmp_path / "secrets.enc", machine_id="m")
    store._file_set("k", "from-file")
    assert await store.get("k") == "from-file"
    # keyring 恢复可用后再次保存：迁移到 keyring，并移除文件副本
    assert await store.set("k", "new") == "keyring"
    assert store._file_get("k") is None
    assert await store.get("k") == "new"
