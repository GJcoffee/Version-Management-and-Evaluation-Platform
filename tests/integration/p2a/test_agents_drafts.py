"""Agent 与草稿：创建、乐观锁、校验。"""

import pytest

from .conftest import API, TOOL_YAML, config_spec


async def validate(client, draft_id):
    r = await client.post(f"{API}/drafts/{draft_id}/validate")
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- agent


async def test_create_agent_creates_main_draft_with_template(api, client):
    out = await api.agent("customer-support", description="客服")
    agent, draft = out["agent"], out["draft"]
    assert agent["id"].startswith("agt_") and agent["kind"] == "config" and agent["archived"] is False
    assert agent["latest_version"] is None and agent["description"] == "客服"
    assert draft["name"] == "main" and draft["revision"] == 1 and draft["base_version_id"] is None
    spec = draft["spec"]
    assert spec["kind"] == "config" and spec["schema"] == "evalp/v1"
    assert spec["runtime"]["max_steps"] == 12 and spec["model"]["params"]["temperature"] == 0.3


async def test_template_uses_first_provider(api):
    await api.provider("deepseek", ["deepseek-chat", "deepseek-reasoner"])
    out = await api.agent("bot")
    assert out["draft"]["spec"]["model"] == {
        "provider": "deepseek",
        "name": "deepseek-chat",
        "params": {"temperature": 0.3, "top_p": 1.0, "max_tokens": 4096, "seed": None},
    }


async def test_code_agent_template(api):
    out = await api.agent("coder", kind="code")
    spec = out["draft"]["spec"]
    assert spec["kind"] == "code" and spec["transport"] == "http"
    assert spec["http"]["base_url"].startswith("http://") and spec["source"]["capture_git"] is False


async def test_create_agent_with_initial_spec(api):
    spec = config_spec(prompts={"system": "初始提示词"})
    out = await api.agent("with-spec", spec=spec)
    assert out["draft"]["spec"]["prompts"]["system"] == "初始提示词"
    assert out["draft"]["spec"]["schema"] == "evalp/v1"  # 补上 schema


async def test_create_agent_kind_mismatch_and_duplicates(api, client):
    r = await client.post(f"{API}/agents", json={"name": "x", "kind": "code", "spec": config_spec()})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    await api.agent("dup")
    r = await client.post(f"{API}/agents", json={"name": "dup", "kind": "config"})
    assert r.status_code == 409 and r.json()["error"]["details"]["name"] == "dup"
    for bad in ("has space", "a:b", "", "x" * 65):
        r = await client.post(f"{API}/agents", json={"name": bad, "kind": "config"})
        assert r.status_code == 400, bad
    assert (await client.post(f"{API}/agents", json={"name": "ok", "kind": "weird"})).status_code == 400
    assert (await api.agent("客服机器人"))["agent"]["name"] == "客服机器人"


async def test_list_get_update_archive_delete(api, client):
    a = (await api.agent("a1"))["agent"]
    b = (await api.agent("b1", kind="code"))["agent"]
    items = (await client.get(f"{API}/agents")).json()["items"]
    assert [i["name"] for i in items] == ["b1", "a1"]
    assert [
        i["name"] for i in (await client.get(f"{API}/agents", params={"kind": "code"})).json()["items"]
    ] == ["b1"]
    assert [i["name"] for i in (await client.get(f"{API}/agents", params={"q": "a1"})).json()["items"]] == [
        "a1"
    ]
    # 按 id 或名称获取
    assert (await client.get(f"{API}/agents/{a['id']}")).json()["name"] == "a1"
    assert (await client.get(f"{API}/agents/a1")).json()["id"] == a["id"]
    assert (await client.get(f"{API}/agents/nope")).status_code == 404
    r = await client.patch(f"{API}/agents/{a['id']}", json={"description": "新描述", "archived": True})
    assert r.json()["archived"] is True and r.json()["description"] == "新描述"
    assert [i["name"] for i in (await client.get(f"{API}/agents")).json()["items"]] == ["b1"]
    assert len((await client.get(f"{API}/agents", params={"include_archived": "true"})).json()["items"]) == 2
    r = await client.patch(f"{API}/agents/{a['id']}", json={"name": "b1"})
    assert r.status_code == 409
    r = await client.patch(f"{API}/agents/{a['id']}", json={"name": "a2", "archived": False})
    assert r.json()["name"] == "a2" and r.json()["archived"] is False
    assert (await client.delete(f"{API}/agents/{b['id']}")).status_code == 204
    assert (await client.get(f"{API}/agents/{b['id']}")).status_code == 404


async def test_cannot_delete_agent_with_versions(api, client):
    await api.provider()
    out = await api.agent("bot", spec=config_spec())
    await api.publish(out["draft"])
    r = await client.delete(f"{API}/agents/{out['agent']['id']}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "AGENT_IN_USE"
    latest = (await client.get(f"{API}/agents/bot")).json()["latest_version"]
    assert latest["seq"] == 1


# ---------------------------------------------------------------- 草稿


async def test_save_draft_bumps_revision(api, client):
    out = await api.agent("bot")
    d = out["draft"]
    spec = {**d["spec"], "prompts": {"system": "改了", "variables": {}}}
    r = await client.put(f"{API}/drafts/{d['id']}", json={"spec": spec, "expected_revision": 1})
    assert r.status_code == 200
    saved = r.json()["draft"]
    assert saved["revision"] == 2 and saved["spec"]["prompts"]["system"] == "改了"
    assert saved["updated_at"] >= d["updated_at"]
    got = (await client.get(f"{API}/drafts/{d['id']}")).json()
    assert got == saved


async def test_save_draft_optimistic_lock(api, client):
    d = (await api.agent("bot"))["draft"]
    await api.save(d, {**d["spec"], "notes": "A 的修改"})  # revision -> 2
    r = await client.put(f"{API}/drafts/{d['id']}", json={"spec": d["spec"], "expected_revision": 1})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "DRAFT_CONFLICT" and err["details"]["current_revision"] == 2
    assert "revision=2" in err["message"]
    # 冲突后草稿没有被覆盖
    assert "notes" in (await api.draft(d["id"]))["spec"]
    # expected_revision 必填
    assert (await client.put(f"{API}/drafts/{d['id']}", json={"spec": d["spec"]})).status_code == 400


async def test_concurrent_saves_only_one_wins(api, client):
    import asyncio

    d = (await api.agent("bot"))["draft"]
    rs = await asyncio.gather(
        *[
            client.put(
                f"{API}/drafts/{d['id']}",
                json={"spec": {**d["spec"], "notes": str(i)}, "expected_revision": 1},
            )
            for i in range(5)
        ]
    )
    assert sorted(r.status_code for r in rs) == [200, 409, 409, 409, 409]
    assert (await api.draft(d["id"]))["revision"] == 2


async def test_draft_accepts_incomplete_spec_but_checks_kind(api, client):
    d = (await api.agent("bot"))["draft"]
    r = await client.put(
        f"{API}/drafts/{d['id']}", json={"spec": {"model": {"provider": "x"}}, "expected_revision": 1}
    )
    assert r.status_code == 200 and r.json()["draft"]["spec"]["kind"] == "config"  # 补上 kind
    d2 = r.json()["draft"]
    r = await client.put(f"{API}/drafts/{d2['id']}", json={"spec": {"kind": "code"}, "expected_revision": 2})
    assert r.status_code == 400
    r = await client.put(
        f"{API}/drafts/{d2['id']}", json={"spec": {"schema": "evalp/v9"}, "expected_revision": 2}
    )
    assert r.status_code == 400
    r = await client.put(f"{API}/drafts/{d2['id']}", json={"spec": ["x"], "expected_revision": 2})
    assert r.status_code == 400
    assert (await client.get(f"{API}/drafts/drf_nope")).status_code == 404


async def test_multiple_drafts(api, client):
    out = await api.agent("bot")
    aid = out["agent"]["id"]
    r = await client.post(f"{API}/agents/{aid}/drafts", json={"name": "experiment"})
    assert r.status_code == 201 and r.json()["draft"]["name"] == "experiment"
    assert (await client.post(f"{API}/agents/{aid}/drafts", json={"name": "experiment"})).status_code == 409
    r = await client.post(
        f"{API}/agents/bot/drafts", json={"name": "with-spec", "spec": config_spec(notes="x")}
    )
    assert r.json()["draft"]["spec"]["notes"] == "x"
    names = [d["name"] for d in (await client.get(f"{API}/agents/{aid}/drafts")).json()]
    assert names == ["main", "experiment", "with-spec"]


# ---------------------------------------------------------------- validate


async def test_validate_ok_returns_resolved_preview(api, client):
    await api.provider()
    await api.asset("prompt", "sys", "你好 {{ variables.company }}")
    await api.asset("tool", "q", TOOL_YAML)
    await api.asset("knowledge", "faq", "# FAQ")
    spec = config_spec(
        prompts={"system": "asset://prompt/sys@latest", "variables": {"company": "A"}},
        tools=[
            "asset://tool/q@latest",
            {"builtin": "knowledge_search", "with": {"sources": ["asset://knowledge/faq@latest"]}},
        ],
        knowledge={"inject": ["asset://knowledge/faq@1"]},
    )
    d = (await api.agent("bot", spec=spec))["draft"]
    v = await validate(client, d["id"])
    assert v["ok"] is True and v["errors"] == [] and v["warnings"] == []
    prev = v["resolved_preview"]
    pin = prev["prompts"]["system"]["$asset"]
    assert (pin["kind"], pin["name"], pin["rev"]) == ("prompt", "sys", 1) and len(pin["hash"]) == 64
    assert prev["tools"][0]["$asset"]["name"] == "q"
    assert prev["tools"][1]["with"]["sources"][0]["$asset"]["name"] == "faq"
    assert prev["knowledge"]["inject"][0]["$asset"]["rev"] == 1
    assert "name" not in prev and "notes" not in prev  # 预览即发布时存储的规范化 Spec


async def test_validate_reports_all_problems_with_paths(api, client):
    await api.provider("deepseek", ["deepseek-chat"])
    await api.asset("prompt", "sys", "x")
    spec = config_spec(
        model={"provider": "nope", "name": "m"},
        prompts={"system": "asset://prompt/missing@latest"},
        tools=["asset://tool/missing@1", "asset://prompt/sys@1"],
    )
    d = (await api.agent("bot", spec=spec))["draft"]
    v = await validate(client, d["id"])
    assert v["ok"] is False and v["resolved_preview"] is None
    assert {e["path"] for e in v["errors"]} == {"tools[1]"}  # 类型不对属于结构错误，会先单独返回
    # 只剩业务错误时
    d2 = (
        await api.agent(
            "bot2",
            spec=config_spec(
                model={"provider": "nope", "name": "m"},
                prompts={"system": "asset://prompt/missing@latest"},
                tools=["asset://tool/missing@1"],
            ),
        )
    )["draft"]
    paths2 = {e["path"]: e["message"] for e in (await validate(client, d2["id"]))["errors"]}
    assert set(paths2) == {"model.provider", "prompts.system", "tools[0]"}


async def test_validate_model_must_be_in_provider_models(api, client):
    await api.provider("deepseek", ["deepseek-chat", "deepseek-reasoner"])
    d = (await api.agent("bot", spec=config_spec(model={"provider": "deepseek", "name": "gpt-4"})))["draft"]
    v = await validate(client, d["id"])
    (e,) = v["errors"]
    assert e["path"] == "model.name" and "deepseek-chat" in e["message"]


async def test_validate_provider_without_models_only_warns(api, client):
    await api.provider("deepseek", [])
    d = (await api.agent("bot", spec=config_spec(model={"provider": "deepseek", "name": "whatever"})))[
        "draft"
    ]
    v = await validate(client, d["id"])
    assert v["ok"] is True and v["errors"] == []
    assert v["warnings"][0]["path"] == "model.name" and "模型列表" in v["warnings"][0]["message"]


async def test_validate_tool_content_and_duplicate_names(api, client):
    await api.provider()
    await api.asset("tool", "t1", TOOL_YAML)
    await api.asset("tool", "t2", TOOL_YAML)  # 工具名相同（query_order）
    d = (
        await api.agent("bot", spec=config_spec(tools=["asset://tool/t1@latest", "asset://tool/t2@latest"]))
    )["draft"]
    v = await validate(client, d["id"])
    assert v["ok"] is False
    assert v["errors"][0]["path"] == "tools[1]" and "重复" in v["errors"][0]["message"]
    # 同一个资产引用两次也算重复
    d2 = (await api.agent("bot2", spec=config_spec(tools=["asset://tool/t1@1", "asset://tool/t1@latest"])))[
        "draft"
    ]
    assert (await validate(client, d2["id"]))["ok"] is False


async def test_validate_prompt_template_errors_and_warnings(api, client):
    await api.provider()
    d = (await api.agent("bot", spec=config_spec(prompts={"system": "{% if x %}没结束"})))["draft"]
    v = await validate(client, d["id"])
    assert v["errors"][0]["path"] == "prompts.system" and "模板语法错误" in v["errors"][0]["message"]
    d2 = (
        await api.agent(
            "bot2",
            spec=config_spec(prompts={"system": "你好 {{ company }} {{ variables.x }}", "variables": {}}),
        )
    )["draft"]
    v2 = await validate(client, d2["id"])
    assert v2["ok"] is True
    assert v2["warnings"][0]["message"].startswith("模板使用了未定义的变量: company（")
    d3 = (
        await api.agent(
            "bot3", spec=config_spec(prompts={"system": "你好 {{ company }}", "variables": {"company": "A"}})
        )
    )["draft"]
    assert (await validate(client, d3["id"]))["warnings"] == []
    d4 = (await api.agent("bot4", spec=config_spec(prompts={"system": "  "})))["draft"]
    assert (await validate(client, d4["id"]))["warnings"][0]["path"] == "prompts.system"


async def test_validate_builtin_without_sources_warns(api, client):
    await api.provider()
    d = (await api.agent("bot", spec=config_spec(tools=[{"builtin": "knowledge_search"}])))["draft"]
    v = await validate(client, d["id"])
    assert v["ok"] is True and v["warnings"][0]["path"] == "tools[0].with.sources"


async def test_validate_invalid_tool_asset_content_cannot_exist_but_deleted_asset_is_error(api, client):
    await api.provider()
    a = await api.asset("tool", "t", TOOL_YAML)
    d = (await api.agent("bot", spec=config_spec(tools=["asset://tool/t@latest"])))["draft"]
    assert (await validate(client, d["id"]))["ok"] is True
    await client.delete(f"{API}/assets/{a['asset']['id']}")
    v = await validate(client, d["id"])
    assert v["ok"] is False and "已被删除" in v["errors"][0]["message"]


async def test_validate_unknown_revision(api, client):
    await api.provider()
    await api.asset("prompt", "p", "x")
    d = (await api.agent("bot", spec=config_spec(prompts={"system": "asset://prompt/p@9"})))["draft"]
    v = await validate(client, d["id"])
    assert "修订不存在" in v["errors"][0]["message"]


@pytest.mark.parametrize(
    ("spec", "path"),
    [
        ({"kind": "code", "transport": "http", "http": {"base_url": "ftp://x"}}, "http.base_url"),
        ({"kind": "code", "transport": "subprocess", "subprocess": {"command": [" "]}}, "subprocess.command"),
    ],
)
async def test_validate_code_spec(api, client, spec, path):
    d = (await api.agent("coder", kind="code", spec=spec))["draft"]
    v = await validate(client, d["id"])
    assert v["ok"] is False and v["errors"][0]["path"] == path


async def test_validate_code_env_refs_warn_when_unset(api, client, monkeypatch):
    monkeypatch.delenv("MY_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("SET_VAR", "1")
    spec = {
        "kind": "code",
        "transport": "http",
        "http": {
            "base_url": "http://x",
            "headers": {"A": "Bearer ${env:MY_AGENT_TOKEN}", "B": "${env:SET_VAR}"},
        },
        "source": {"capture_git": False},
    }
    d = (await api.agent("coder", kind="code", spec=spec))["draft"]
    v = await validate(client, d["id"])
    assert v["ok"] is True
    assert [w["path"] for w in v["warnings"]] == ["http.headers.A"] and "MY_AGENT_TOKEN" in v["warnings"][0][
        "message"
    ]


async def test_validate_structure_errors_have_field_paths(api, client):
    d = (
        await api.agent(
            "bot",
            spec={
                "kind": "config",
                "model": {"provider": "", "name": "m"},
                "runtime": {"max_steps": 0},
                "typo": 1,
            },
        )
    )["draft"]
    v = await validate(client, d["id"])
    paths = {e["path"] for e in v["errors"]}
    assert {"model.provider", "runtime.max_steps", "typo"} <= paths
