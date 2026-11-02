"""版本里程碑与归档：设为阶段性成果、取消、移出归档，以及列表过滤。"""

import pytest

from .conftest import API, config_spec


@pytest.fixture
async def seven(api, client):
    """一个 provider + agent bot 的 7 个版本（v1…v7），返回 id 列表（下标 0 = v1）。"""
    await api.provider()
    out = await api.agent("bot", spec=config_spec())
    draft, ids = out["draft"], []
    for n in range(1, 8):
        model = {"provider": "deepseek", "name": "deepseek-chat", "params": {"temperature": n / 10}}
        draft = (await api.save(draft, config_spec(model=model)))["draft"]
        ids.append((await api.publish(draft))["version"]["id"])
    return ids


async def listing(client, query=""):
    return (await client.get(f"{API}/agents/bot/versions{query}")).json()["items"]


def seqs(items):
    return [i["seq"] for i in items]


async def milestone(client, ref, **body):
    return await client.post(f"{API}/versions/{ref}/milestone", json=body)


async def test_milestone_archives_all_earlier_plain_versions_by_default(client, seven):
    r = await milestone(client, seven[3], title="第一阶段：语气优化")
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["milestone"] is True and m["milestone_title"] == "第一阶段：语气优化"
    assert m["archived_into"] is None and m["archived_count"] == 3

    items = await listing(client)  # 默认返回全部
    assert seqs(items) == [7, 6, 5, 4, 3, 2, 1]
    by_seq = {i["seq"]: i for i in items}
    assert [by_seq[n]["archived_into"] for n in (1, 2, 3)] == [seven[3]] * 3
    assert by_seq[5]["archived_into"] is None and by_seq[4]["archived_count"] == 3
    assert by_seq[2]["milestone"] is False and by_seq[2]["archived_count"] == 0

    assert seqs(await listing(client, "?archived=false")) == [7, 6, 5, 4]
    assert seqs(await listing(client, "?archived=true")) == [3, 2, 1]
    # 过滤后分页照常
    page = (await client.get(f"{API}/agents/bot/versions?archived=false&limit=2")).json()
    assert seqs(page["items"]) == [7, 6] and page["next_cursor"]


async def test_earlier_milestone_stays_visible_and_is_not_archived_by_a_later_one(client, seven):
    await milestone(client, seven[1], title="一")
    r = await milestone(client, seven[5], title="二")
    assert r.json()["archived_count"] == 3  # v3 v4 v5；v2 是里程碑、v1 已归档到 v2
    visible = await listing(client, "?archived=false")
    assert seqs(visible) == [7, 6, 2]
    assert {i["seq"]: i["archived_count"] for i in visible} == {7: 0, 6: 3, 2: 1}


async def test_explicit_version_ids_archive_only_those(client, seven):
    r = await milestone(client, seven[4], version_ids=[seven[0], seven[2]])
    assert r.status_code == 200 and r.json()["archived_count"] == 2
    assert seqs(await listing(client, "?archived=false")) == [7, 6, 5, 4, 2]
    # 空列表：只标记里程碑
    r = await milestone(client, seven[6], version_ids=[])
    assert r.json()["milestone"] is True and r.json()["archived_count"] == 0


async def test_explicit_version_ids_must_be_earlier_unarchived_non_milestones(client, seven):
    await milestone(client, seven[3], version_ids=[seven[0]])  # v4 是里程碑，v1 已归档到它
    cases = {
        "必须比目标版本更早": (seven[4], [seven[5]]),
        "是阶段性成果": (seven[5], [seven[3]]),
        "已经归档": (seven[5], [seven[0]]),
    }
    for reason, case in cases.items():
        r = await milestone(client, case[0], version_ids=case[1])
        assert r.status_code == 400, (reason, r.text)
        err = r.json()["error"]["details"]["errors"][0]
        assert err["path"] == "version_ids[0]" and reason in err["message"]
    # 一项不合格则整体不生效
    r = await milestone(client, seven[5], version_ids=[seven[1], seven[0]])
    assert r.status_code == 400
    assert seqs(await listing(client, "?archived=false")) == [7, 6, 5, 4, 3, 2]
    assert (await milestone(client, seven[5], version_ids=["ver_nope"])).status_code == 404


async def test_explicit_version_ids_from_another_agent_are_rejected(api, client, seven):
    other = await api.agent("other", spec=config_spec())
    foreign = (await api.publish(other["draft"]))["version"]["id"]
    r = await milestone(client, seven[4], version_ids=[foreign])
    assert r.status_code == 400 and "不属于同一个 agent" in r.text


async def test_milestone_and_archived_versions_cannot_be_archived_or_promoted(client, seven):
    await milestone(client, seven[2])  # v3 里程碑，v1 v2 归档
    r = await milestone(client, seven[0])
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"  # 已归档的不能设为里程碑
    r = await milestone(client, seven[4], version_ids=[seven[2]])
    assert r.status_code == 400 and "不能被归档" in r.text  # 里程碑不能被归档到别的里程碑


async def test_archived_versions_stay_fully_usable(client, seven):
    await milestone(client, seven[3])
    await client.post(f"{API}/versions/{seven[0]}/tags", json={"tag": "old"})
    for ref in ("bot:v1", "bot:old", seven[0]):
        r = await client.get(f"{API}/versions/{ref}")
        assert r.status_code == 200 and r.json()["seq"] == 1 and r.json()["archived_into"] == seven[3]
    r = await client.get(f"{API}/versions/diff", params={"left": "bot:v1", "right": "bot:v7"})
    assert r.status_code == 200
    r = await client.get(f"{API}/versions/{seven[3]}")
    assert r.json()["archived_count"] == 3
    # latest 不受归档影响
    assert (await client.get(f"{API}/versions/bot:latest")).json()["seq"] == 7


async def test_re_milestone_updates_title_and_archives_more(client, seven):
    await milestone(client, seven[2], title="一", version_ids=[seven[0]])
    r = await milestone(client, seven[2])  # 不给 title：阶段名保持，补充归档剩下的 v2
    assert r.json()["milestone_title"] == "一" and r.json()["archived_count"] == 2
    r = await milestone(client, seven[2], title="  ")  # 空串清空阶段名
    assert r.json()["milestone_title"] is None and r.json()["archived_count"] == 2


async def test_unarchive_one_or_all_and_remove_milestone(client, seven):
    await milestone(client, seven[3], title="一")
    r = await client.post(f"{API}/versions/bot:v2/unarchive")
    assert r.status_code == 200 and r.json()["archived_into"] is None
    assert seqs(await listing(client, "?archived=false")) == [7, 6, 5, 4, 2]
    assert (await client.get(f"{API}/versions/{seven[3]}")).json()["archived_count"] == 2

    r = await client.post(f"{API}/versions/bot:v2/unarchive")  # 没有归档
    assert r.status_code == 409

    r = await client.post(f"{API}/versions/{seven[3]}/unarchive")  # 对里程碑 = 全部移出，里程碑保留
    assert r.json()["milestone"] is True and r.json()["archived_count"] == 0
    assert seqs(await listing(client, "?archived=true")) == []

    await milestone(client, seven[3])
    r = await client.delete(f"{API}/versions/{seven[3]}/milestone")
    assert r.status_code == 200
    assert r.json()["milestone"] is False and r.json()["milestone_title"] is None
    assert seqs(await listing(client, "?archived=false")) == [7, 6, 5, 4, 3, 2, 1]
    assert (await client.delete(f"{API}/versions/{seven[3]}/milestone")).status_code == 409


async def test_after_removing_milestone_archived_version_can_become_one(client, seven):
    await milestone(client, seven[3])
    await client.delete(f"{API}/versions/{seven[3]}/milestone")
    assert (await milestone(client, seven[1])).status_code == 200
