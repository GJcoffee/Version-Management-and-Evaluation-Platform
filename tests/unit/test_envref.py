import pytest
from evalp.errors import ValidationFailed
from evalp.utils.envref import EnvRefError, find_env_refs, resolve_env, resolve_env_deep


def test_find_refs_dedup_order():
    assert find_env_refs("a ${env:B} ${env:A} ${env:B} ${notenv:C} ${env:1bad}") == ["B", "A"]


def test_resolve_from_environ(monkeypatch):
    monkeypatch.setenv("EVALP_TEST_TOKEN", "秘密")
    assert resolve_env("Bearer ${env:EVALP_TEST_TOKEN}!") == "Bearer 秘密!"
    assert resolve_env("没有引用") == "没有引用"


def test_resolve_custom_env_and_empty_value():
    assert resolve_env("${env:X}-${env:Y}", {"X": "1", "Y": ""}) == "1-"


def test_missing_raises_clear_error(monkeypatch):
    monkeypatch.delenv("EVALP_TEST_MISSING", raising=False)
    with pytest.raises(EnvRefError) as ei:
        resolve_env("http://x/${env:EVALP_TEST_MISSING}")
    assert "EVALP_TEST_MISSING" in ei.value.message
    assert ei.value.details == {"name": "EVALP_TEST_MISSING"}
    assert isinstance(ei.value, ValidationFailed)


def test_resolve_deep():
    out = resolve_env_deep({"a": ["${env:K}", 1], "b": {"c": "x${env:K}"}}, {"K": "v"})
    assert out == {"a": ["v", 1], "b": {"c": "xv"}}
