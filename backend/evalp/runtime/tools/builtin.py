"""内置工具 ``knowledge_search``（详设 6.9）。

知识资产按段落切块（每块 ≤ 500 字），jieba 分词后用 rank_bm25 建索引；索引按
"(资产名, 内容哈希)" 序列缓存在进程内存里，同一份知识反复评测只建一次。
返回 top_k 条 ``{source, chunk_id, text}``，没有命中任何查询词时返回空列表。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from evalp.runtime.tools.base import ToolRunner
from evalp.runtime.tools.result import ToolResult, error_result

CHUNK_MAX_CHARS = 500
INDEX_CACHE_SIZE = 16
MAX_TOP_K = 50

_PARA_SPLIT = re.compile(r"\n\s*\n")
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])|(?<=[.])\s+|\n")
_WORD = re.compile(r"\w", re.UNICODE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
_STOPWORDS = frozenset(
    "的 了 是 在 和 与 及 或 吗 呢 吧 啊 呀 嘛 我 你 他 她 它 们 这 那 有 就 都 也 还 要 会 能 "
    "可以 请问 什么 怎么 如何 怎样 哪些 哪个 为什么 是否 一个 the a an of to in is are and or".split()
)

# jieba 首次分词会初始化词典并打印日志
logging.getLogger("jieba").setLevel(logging.WARNING)


@dataclass(frozen=True)
class Chunk:
    source: str
    chunk_id: str
    text: str


def _split_long(text: str, limit: int = CHUNK_MAX_CHARS) -> list[str]:
    """把超长段落按句子边界拆成 ≤ limit 的若干块，单句仍超长时硬切。"""
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    buf = ""
    for sent in (s for s in _SENT_SPLIT.split(text) if s):
        while len(sent) > limit:  # 单句超长：硬切
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.append(sent[:limit])
            sent = sent[limit:]
        if len(buf) + len(sent) > limit:
            pieces.append(buf)
            buf = sent
        else:
            buf += sent
    if buf:
        pieces.append(buf)
    return [p.strip() for p in pieces if p.strip()]


def _paragraphs(text: str) -> list[str]:
    """按空行分段；仅含 Markdown 标题的段落并入下一段，避免产生没有信息量的碎块。"""
    paras: list[str] = []
    pending_heading = ""
    for p in _PARA_SPLIT.split(text.replace("\r\n", "\n")):
        p = p.strip()
        if not p:
            continue
        if _HEADING.match(p) and "\n" not in p:
            pending_heading = f"{pending_heading}\n{p}" if pending_heading else p
            continue
        paras.append(f"{pending_heading}\n{p}" if pending_heading else p)
        pending_heading = ""
    if pending_heading:
        paras.append(pending_heading)
    return paras


def _jsonl_records(content: str) -> list[dict[str, Any]] | None:
    """内容是 JSONL（每行 ``{id?, text, meta?}``）时返回记录，否则 None。"""
    lines = [ln for ln in content.splitlines() if ln.strip()]
    if not lines or not lines[0].lstrip().startswith("{"):
        return None
    records: list[dict[str, Any]] = []
    for ln in lines:
        try:
            obj = json.loads(ln)
        except ValueError:
            return None
        if not isinstance(obj, dict) or not isinstance(obj.get("text"), str):
            return None
        records.append(obj)
    return records


def chunk_source(source: str, content: str) -> list[Chunk]:
    """把一份知识切成块。chunk_id 形如 ``{source}#{序号}``（JSONL 且记录带 id 时为 ``{source}#{id}``）。"""
    chunks: list[Chunk] = []
    records = _jsonl_records(content)
    if records is not None:
        for i, rec in enumerate(records):
            rid = str(rec["id"]) if rec.get("id") is not None else str(i)
            parts = _split_long(rec["text"].strip())
            for j, part in enumerate(parts):
                cid = f"{source}#{rid}" + (f".{j}" if len(parts) > 1 else "")
                chunks.append(Chunk(source, cid, part))
        return chunks
    n = 0
    for para in _paragraphs(content):
        for part in _split_long(para):
            chunks.append(Chunk(source, f"{source}#{n}", part))
            n += 1
    return chunks


def tokenize(text: str) -> list[str]:
    """jieba 分词（搜索引擎模式），小写化并去掉标点和停用词。"""
    import jieba

    out: list[str] = []
    for tok in jieba.lcut_for_search(text.lower()):
        tok = tok.strip()
        if tok and _WORD.search(tok) and tok not in _STOPWORDS:
            out.append(tok)
    return out


class KnowledgeIndex:
    """若干知识来源合并后的 BM25 索引。"""

    def __init__(self, sources: list[tuple[str, str]]) -> None:
        from rank_bm25 import BM25Plus

        self.chunks: list[Chunk] = []
        for name, content in sources:
            self.chunks.extend(chunk_source(name, content))
        self._tokens = [tokenize(c.text) for c in self.chunks]
        self._token_sets = [set(t) for t in self._tokens]
        self._bm25 = BM25Plus(self._tokens) if any(self._tokens) else None

    def search(self, query: str, top_k: int) -> list[Chunk]:
        """返回至少命中一个查询词的前 top_k 块，按相关度降序。"""
        q = tokenize(query)
        if not q or self._bm25 is None:
            return []
        qset = set(q)
        scores = self._bm25.get_scores(q)
        ranked = sorted(
            (i for i, ts in enumerate(self._token_sets) if ts & qset),
            key=lambda i: (-scores[i], i),
        )
        return [self.chunks[i] for i in ranked[:top_k]]


_cache: OrderedDict[tuple[tuple[str, str], ...], KnowledgeIndex] = OrderedDict()
_cache_lock = threading.Lock()


def get_index(sources: list[tuple[str, str, str]]) -> KnowledgeIndex:
    """按 (资产名, 内容哈希) 取缓存的索引，没有则构建（同步，可能较慢，请放进线程里调用）。"""
    key = tuple((name, h) for name, _content, h in sources)
    with _cache_lock:  # 构建期间持锁，避免并发重复构建同一份索引
        idx = _cache.get(key)
        if idx is not None:
            _cache.move_to_end(key)
            return idx
        idx = KnowledgeIndex([(name, content) for name, content, _h in sources])
        _cache[key] = idx
        while len(_cache) > INDEX_CACHE_SIZE:
            _cache.popitem(last=False)
        return idx


def clear_index_cache() -> None:
    """清空索引缓存（测试用）。"""
    with _cache_lock:
        _cache.clear()


class KnowledgeSearchTool(ToolRunner):
    """``knowledge_search(query)``：在注入的知识资产里做 BM25 检索。"""

    name = "knowledge_search"
    description = "在知识库中检索与问题相关的内容片段，返回 top_k 条 {source, chunk_id, text}。"
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "检索问题或关键词"}},
        "required": ["query"],
    }

    def __init__(self, sources: list[tuple[str, str, str]], top_k: int = 5, name: str = "knowledge_search"):
        self.name = name
        self.sources = list(sources)
        self.top_k = max(1, min(top_k, MAX_TOP_K))

    async def run(self, args: dict[str, Any], timeout: float | None) -> ToolResult:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return error_result("参数 query 必须是非空字符串")
        index = await asyncio.to_thread(get_index, self.sources)
        hits = await asyncio.to_thread(index.search, query, self.top_k)
        return ToolResult(output=[{"source": c.source, "chunk_id": c.chunk_id, "text": c.text} for c in hits])
