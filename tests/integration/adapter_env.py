"""适配器环境测试的公共辅助：凭据、HiAgent 接入配置、把配置设为生产。"""

import json
from typing import Any

API = "/api/v1"


async def make_credential(client, secret: Any = "private-test-key") -> str:
    body = secret if isinstance(secret, str) else json.dumps(secret)
    response = await client.post(f"{API}/adapters/credentials", json={
        "name": "test", "secret": body, "adapter_id": "hiagent",
    })
    assert response.status_code == 201, response.text
    assert "private-test-key" not in response.text
    return response.json()["ref"]


def hiagent_binding(ref: str, major: int = 1, **config: Any) -> dict:
    return {"adapter": "hiagent", "major": major, "credential_ref": ref, "config": {
        "base_url": "https://upstream.example/api/proxy",
        "app_id": "app-test", "user_id": "user-test", "published_version": "47", **config,
    }}


async def edit_test(client, binding: dict, reason: str = "编辑测试") -> dict:
    state = (await client.get(f"{API}/adapter-environments")).json()
    response = await client.put(f"{API}/adapter-environments/test", json={
        "binding": binding, "expected_revision": state["test"]["revision"], "reason": reason,
    })
    assert response.status_code == 200, response.text
    return response.json()


async def push(client, reason: str = "") -> dict:
    state = (await client.get(f"{API}/adapter-environments")).json()
    response = await client.post(f"{API}/adapter-environments/production/push", json={
        "test_revision": state["test"]["revision"], "reason": reason,
    })
    assert response.status_code == 200, response.text
    return response.json()


async def configure_production(client, binding: dict) -> dict:
    """在测试里编辑并推送到生产；生产有了这份配置后，测试与生产内容相同。"""
    await edit_test(client, binding)
    return await push(client)
