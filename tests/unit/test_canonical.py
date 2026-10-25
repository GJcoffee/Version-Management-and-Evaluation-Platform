import math

import pytest
from evalp.utils.canonical import canonical_json, sha256_hex
from evalp.utils.time import now_iso, parse_iso


def test_sorted_compact_unicode():
    assert canonical_json({"b": 1, "a": {"z": [1, 2], "y": "中文"}}) == '{"a":{"y":"中文","z":[1,2]},"b":1}'


def test_key_order_does_not_change_hash():
    a = {"x": 1, "y": [1, {"p": 1, "q": 2}]}
    b = {"y": [1, {"q": 2, "p": 1}], "x": 1}
    assert sha256_hex(canonical_json(a)) == sha256_hex(canonical_json(b))


def test_float_normalization():
    assert canonical_json({"t": 1.0}) == canonical_json({"t": 1}) == '{"t":1}'
    assert canonical_json({"t": 0.3}) == '{"t":0.3}'
    assert canonical_json({"t": -0.0}) == '{"t":0}'
    assert canonical_json([1e3, 2.5e-7]) == "[1000,2.5e-07]"
    assert canonical_json({"b": True, "n": None}) == '{"b":true,"n":null}'  # bool 不被当作数字


def test_rejects_nan_and_unknown_types():
    with pytest.raises(ValueError):
        canonical_json(math.nan)
    with pytest.raises(TypeError):
        canonical_json({"x": object()})


def test_sha256_hex():
    assert sha256_hex("abc") == sha256_hex(b"abc")
    assert sha256_hex("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_now_iso_format():
    s = now_iso()
    assert s.endswith("Z") and len(s) == 24 and s[10] == "T" and s[19] == "."
    assert parse_iso(s).tzinfo is not None
