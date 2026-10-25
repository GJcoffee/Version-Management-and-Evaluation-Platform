import asyncio

import pytest
from evalp.api.sse import PING_FRAME, format_sse, sse_response, with_heartbeat


def test_format_full_frame():
    assert format_sse({"a": "中"}, event="llm.end", id=7) == 'id: 7\nevent: llm.end\ndata: {"a":"中"}\n\n'


def test_delta_without_id():
    frame = format_sse({"t": "x"}, event="llm.delta")
    assert not frame.startswith("id:") and "\nid:" not in frame
    assert frame.endswith("\n\n")


def test_multiline_string_data_and_idle():
    assert format_sse("a\nb", event="x") == "event: x\ndata: a\ndata: b\n\n"
    assert format_sse({}, event="idle") == "event: idle\ndata: {}\n\n"


def test_pydantic_model_data():
    from pydantic import BaseModel

    class M(BaseModel):
        x: int

    assert format_sse(M(x=1)) == 'data: {"x":1}\n\n'


def test_ping_frame_is_comment():
    assert PING_FRAME == ": ping\n\n"


async def test_with_heartbeat_inserts_pings():
    async def src():
        yield "first"
        await asyncio.sleep(0.12)
        yield "second"

    out = [f async for f in with_heartbeat(src(), 0.03)]
    assert out[0] == "first" and out[-1] == "second"
    assert out.count(PING_FRAME) >= 2


async def test_with_heartbeat_cancels_source_on_close():
    closed = asyncio.Event()

    async def src():
        try:
            while True:
                await asyncio.sleep(10)
                yield "x"
        finally:
            closed.set()

    gen = with_heartbeat(src(), 0.01)
    assert await gen.__anext__() == PING_FRAME
    await gen.aclose()
    assert closed.is_set()


async def test_sse_response_headers():
    async def src():
        yield format_sse({"ok": 1}, event="e", id=1)

    resp = sse_response(src())
    assert resp.media_type == "text/event-stream"
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["x-accel-buffering"] == "no"


@pytest.mark.parametrize("hb", [None, 0.05])
async def test_sse_response_body(hb):
    async def src():
        yield "data: 1\n\n"

    resp = sse_response(src(), heartbeat=hb)
    chunks = [c async for c in resp.body_iterator]
    assert chunks == ["data: 1\n\n"]
