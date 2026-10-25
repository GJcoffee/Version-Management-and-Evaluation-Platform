import pytest
from evalp.ids import PREFIXES, is_id, new_id, split_id


def test_all_prefixes_from_design():
    expected = {
        "agt",
        "drf",
        "ver",
        "ast",
        "arv",
        "cas",
        "prv",
        "ses",
        "trn",
        "evb",
        "evr",
        "evi",
        "cmp",
        "job",
    }
    assert set(PREFIXES) == expected


def test_format():
    v = new_id("agt")
    assert v.startswith("agt_") and len(v) == 4 + 26
    assert split_id(v)[0] == "agt"
    assert is_id(v) and is_id(v, "agt") and not is_id(v, "ver")


def test_unknown_prefix():
    with pytest.raises(ValueError):
        new_id("xxx")
    assert not is_id("agt_short")
    with pytest.raises(ValueError):
        split_id("nope")


def test_strictly_increasing():
    ids = [new_id("evi") for _ in range(5000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
