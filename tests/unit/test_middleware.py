"""JSON 产物发现、字段选择与受限下载。"""

import asyncio
import json

import httpx
import pytest
import respx
from evalp.config import ArtifactsConfig
from evalp.errors import EvalpError
from evalp.runtime.messages import Message
from evalp.runtime.middleware.json_artifact import (
    candidates,
    compose_input,
    decode_artifact,
    fetch_artifact,
    pointer,
    select_candidate,
)
from evalp.schemas.middleware import JsonArtifactConfig

URL = "https://artifacts.example/story.json?signature=a%2Bb"


@pytest.mark.parametrize("text", [
    URL, f"[下载 JSON]({URL})", json.dumps({"jsonUrl": URL}),
    f"```json\n{json.dumps({'result': {'url': URL}})}\n```",
])
def test_candidate_formats_and_stable_signature(text):
    final = Message(role="assistant", content=text)
    items = candidates("source", final, JsonArtifactConfig())
    assert [item.url for item in items] == [URL]
    assert items == candidates("source", final, JsonArtifactConfig())
    assert items[0].id != candidates("other", final, JsonArtifactConfig())[0].id


@pytest.mark.parametrize("tail", [".", ",", ";", "!", "?", ":", "?!", "。"])
def test_bare_link_drops_trailing_sentence_punctuation(tail):
    url = "https://artifacts.example/story.json"
    final = Message(role="assistant", content=f"结果见 {url}{tail} 请查收")
    assert [item.url for item in candidates("source", final, JsonArtifactConfig())] == [url]


def test_explicit_link_pointer_and_escaped_keys():
    config = JsonArtifactConfig(url_pointer="/a~1b/~0url")
    final = Message(role="assistant", content=json.dumps({"a/b": {"~url": "https://artifacts.example/file"}}))
    assert candidates("source", final, config)[0].url.endswith("/file")
    with pytest.raises(EvalpError, match="回复链接字段"):
        candidates("source", final, JsonArtifactConfig(url_pointer="/missing"))
    assert pointer({"a/b": {"~": [1, 2]}}, "/a~1b/~0/1") == 2
    with pytest.raises(EvalpError):
        pointer([1, 2], "/-1")
    with pytest.raises(ValueError):
        JsonArtifactConfig(content_pointer="/broken~")


def test_multiple_candidates_require_explicit_choice():
    items = candidates("source", Message(role="assistant", content=f"{URL}\nhttps://artifacts.example/b.json"),
                       JsonArtifactConfig())
    with pytest.raises(EvalpError) as exc:
        select_candidate(items, JsonArtifactConfig())
    assert exc.value.code == "ARTIFACT_AMBIGUOUS"
    assert select_candidate(items, JsonArtifactConfig(selected_candidate_id=items[1].id)) == items[1]
    with pytest.raises(EvalpError):
        select_candidate(items, JsonArtifactConfig(selected_candidate_id="arbitrary"))


def test_structured_and_markdown_signed_urls_are_not_truncated_or_duplicated():
    url = "https://artifacts.example/story.json?signature=a(b)"
    for text in [json.dumps({"json_url": url}), f"[JSON]({url})"]:
        items = candidates("source", Message(role="assistant", content=text), JsonArtifactConfig())
        assert [item.url for item in items] == [url]
    assert not candidates("source", Message(role="assistant", content="https://cdn.example/pic.png"),
                          JsonArtifactConfig())


@pytest.mark.parametrize("raw", [b"not json", b"null", b"3", b'{"x":NaN}', b'{"x":1e999}', b"\xff"])
def test_invalid_json_rejected(raw):
    with pytest.raises(EvalpError) as exc:
        decode_artifact(raw, URL, JsonArtifactConfig())
    assert exc.value.code == "ARTIFACT_INVALID_JSON"


def test_selected_content_and_no_silent_input_truncation():
    result = decode_artifact(b'{"story":{"scenes":[1,2]},"other":3}', URL,
                             JsonArtifactConfig(content_pointer="/story/scenes"))
    msg = compose_input(Message.user("业务前缀：不添加对白"), result, ArtifactsConfig())
    assert msg.text().startswith("业务前缀：不添加对白\n\n")
    assert msg.text().endswith("[1,2]") and URL in msg.text()
    with pytest.raises(EvalpError) as exc:
        compose_input(Message.user("不添加对白"), result, ArtifactsConfig(max_input_bytes=10))
    assert exc.value.code == "ARTIFACT_TOO_LARGE"


async def test_streaming_fetch_preserves_url_and_forwards_no_credentials():
    with respx.mock() as router:
        route = router.get(URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        raw = await fetch_artifact(URL, ArtifactsConfig(allowed_hosts="artifacts.example"))
        assert json.loads(raw) == {"ok": True}
        assert route.called
        request = route.calls[0].request
        assert "authorization" not in request.headers and "cookie" not in request.headers
        assert request.url.query == b"signature=a%2Bb"


async def test_redirect_target_checked_and_response_cookie_not_forwarded():
    with respx.mock() as router:
        router.get(URL).mock(return_value=httpx.Response(302, headers={
            "Location": "https://artifacts.example/next.json", "Set-Cookie": "secret=value",
        }))
        next_route = router.get("https://artifacts.example/next.json").mock(
            return_value=httpx.Response(302, headers={"Location": "http://127.0.0.1/admin.json"}))
        with pytest.raises(EvalpError):
            await fetch_artifact(URL, ArtifactsConfig(allowed_hosts="artifacts.example"))
        assert "cookie" not in next_route.calls[0].request.headers
        assert len(router.calls) == 2


@pytest.mark.parametrize("response,limit,code", [
    (httpx.Response(403), 1000, "ARTIFACT_FETCH_FAILED"),
    (httpx.Response(200, content=b"x" * 100), 10, "ARTIFACT_TOO_LARGE"),
])
async def test_download_failures(response, limit, code):
    with respx.mock() as router:
        router.get(URL).mock(return_value=response)
        with pytest.raises(EvalpError) as exc:
            await fetch_artifact(URL, ArtifactsConfig(allowed_hosts="artifacts.example",
                                                     max_download_bytes=limit))
        assert exc.value.code == code
        assert "signature" not in exc.value.message


async def test_empty_allowlist_does_not_issue_request():
    with respx.mock() as router:
        with pytest.raises(EvalpError):
            await fetch_artifact(URL, ArtifactsConfig())
        assert not router.calls


async def test_download_has_its_own_total_timeout():
    entered = asyncio.Event()

    async def slow(request):
        entered.set()
        await asyncio.Event().wait()
    with respx.mock(assert_all_called=False) as router:
        router.get(URL).mock(side_effect=slow)
        with pytest.raises(EvalpError) as exc:
            await fetch_artifact(URL, ArtifactsConfig(allowed_hosts="artifacts.example", timeout_s=0.2))
        assert exc.value.code == "ARTIFACT_FETCH_FAILED"
        assert entered.is_set()
