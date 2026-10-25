"""knowledge_search：分块、中文检索、索引缓存。"""

import json

import pytest
from evalp.runtime.messages import ToolCall
from evalp.runtime.tools.builtin import (
    CHUNK_MAX_CHARS,
    chunk_source,
    clear_index_cache,
    get_index,
    tokenize,
)
from evalp.runtime.tools.executor import BuiltinTool, ToolSet

REFUND = """# 退款政策

自收到商品之日起七天内，在商品完好、不影响二次销售的前提下，用户可以申请无理由退款。

退款申请通过后，款项将在三个工作日内原路退回到支付账户。
"""
SHIPPING = """## 物流配送

我们默认使用顺丰快递发货，下单后二十四小时内出库。偏远地区可能需要额外两到三天。

如需修改收货地址，请在商品发货之前联系在线客服。
"""
INVOICE = "发票说明\n\n电子发票会在订单完成后发送到您的邮箱。企业用户可以申请增值税专用发票。"


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_index_cache()


def sources():
    return [("退款.md", REFUND, "h1"), ("物流.md", SHIPPING, "h2"), ("发票.txt", INVOICE, "h3")]


async def search(query: str, top_k: int = 3, srcs=None):
    ts = ToolSet.build([BuiltinTool(sources=srcs or sources(), top_k=top_k)])
    r = await ts.execute(ToolCall(id="c", name="knowledge_search", arguments={"query": query}))
    assert not r.is_error, r.output
    return r.output


async def test_chinese_retrieval_ranks_relevant_first():
    hits = await search("怎么申请退款？")
    assert hits[0]["source"] == "退款.md" and "无理由退款" in hits[0]["text"] + hits[1]["text"]
    assert {"source", "chunk_id", "text"} == set(hits[0])
    hits = await search("发货用什么快递")
    assert hits[0]["source"] == "物流.md" and "顺丰" in hits[0]["text"]
    hits = await search("增值税发票怎么开")
    assert hits[0]["source"] == "发票.txt"
    hits = await search("修改收货地址")
    assert hits[0]["source"] == "物流.md" and "收货地址" in hits[0]["text"]


async def test_top_k_and_no_match():
    assert len(await search("退款 发货 发票", top_k=2)) == 2
    assert await search("量子力学薛定谔") == []
    assert await search("？！。，") == []  # 全是标点


async def test_english_and_mixed_query():
    srcs = [("a.md", "Use the API key in the Authorization header.\n\nRate limits apply per minute.", "x")]
    hits = await search("authorization header", srcs=srcs)
    assert hits and "Authorization" in hits[0]["text"]


async def test_empty_query_and_empty_sources():
    ts = ToolSet.build([BuiltinTool(sources=sources())])
    r = await ts.execute(ToolCall(id="c", name="knowledge_search", arguments={"query": "  "}))
    assert r.is_error
    r = await ts.execute(ToolCall(id="c", name="knowledge_search", arguments={}))
    assert r.is_error
    assert await search("anything", srcs=[("e.md", "", "h")]) == []


def test_chunking_by_paragraph_merges_headings():
    chunks = chunk_source("退款.md", REFUND)
    assert [c.chunk_id for c in chunks] == ["退款.md#0", "退款.md#1"]
    assert chunks[0].text.startswith("# 退款政策\n自收到商品")  # 标题并入下一段
    assert chunks[1].text.startswith("退款申请通过后")


def test_long_paragraph_split_within_limit():
    sentence = "这是一个用来测试分块长度限制的句子。"
    text = sentence * 100  # 单段 1800+ 字
    chunks = chunk_source("long", text)
    assert len(chunks) >= 4 and all(len(c.text) <= CHUNK_MAX_CHARS for c in chunks)
    assert "".join(c.text for c in chunks) == text  # 不丢字
    hard = chunk_source("hard", "无标点" * 400)  # 没有句子边界：硬切
    assert all(len(c.text) <= CHUNK_MAX_CHARS for c in hard) and len(hard) == 3
    mixed = chunk_source("m", "短段落\n\n" + sentence * 60 + "\n\n另一个短段落")
    assert all(len(c.text) <= CHUNK_MAX_CHARS for c in mixed) and len(mixed) >= 4


def test_jsonl_knowledge():
    lines = [
        {"id": "faq1", "text": "如何重置密码？在登录页点击忘记密码。", "meta": {"k": 1}},
        {"text": "客服电话是 400-000-0000。"},
    ]
    content = "\n".join(json.dumps(x, ensure_ascii=False) for x in lines)
    chunks = chunk_source("faq.jsonl", content)
    assert [c.chunk_id for c in chunks] == ["faq.jsonl#faq1", "faq.jsonl#1"]
    assert chunks[0].text.startswith("如何重置密码")
    assert chunk_source("bad.jsonl", '{"text": "a"}\nnot json')[0].text.startswith(
        '{"text"'
    )  # 不是 JSONL 则按普通文本


def test_tokenize_drops_stopwords_and_punctuation():
    toks = tokenize("请问，怎么申请退款？")
    assert "退款" in toks and "，" not in toks and "？" not in toks and "怎么" not in toks


def test_index_cached_by_content_hash():
    a = get_index(sources())
    assert get_index(sources()) is a
    changed = [("退款.md", REFUND + "\n新增一段", "h1-new"), *sources()[1:]]
    assert get_index(changed) is not a
    assert get_index(sources()) is a  # 旧的仍在缓存里


async def test_cache_shared_across_toolsets(monkeypatch):
    import evalp.runtime.tools.builtin as b

    built = []
    orig = b.KnowledgeIndex.__init__

    def counting(self, srcs):
        built.append(1)
        orig(self, srcs)

    monkeypatch.setattr(b.KnowledgeIndex, "__init__", counting)
    await search("退款")
    await search("发货")
    assert len(built) == 1
