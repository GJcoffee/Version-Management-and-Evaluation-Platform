"""yamlio.dump_preserving：更新数据时保留注释、键顺序与缩进风格。"""

from __future__ import annotations

import yaml
from evalp.actions import merge_patch
from evalp.yamlio import dump_preserving, plain_dump

ORIGINAL = """\
# 客服 agent
schema: evalp/v1
kind: config
model:
  provider: deepseek          # 改成你登记的 Provider
  name: deepseek-chat
  params:
    temperature: 0.3          # 越低越稳定
prompts:
  system: file:prompts/system.md     # push 时上传
tools:
  # 查订单
  - file:tools/query-order.yaml
runtime:
  max_steps: 8
"""


def load(text: str):
    return yaml.safe_load(text)


def test_comments_survive_value_changes():
    data = load(ORIGINAL)
    data["model"]["params"]["temperature"] = 0.7
    data["runtime"]["max_steps"] = 4
    out = dump_preserving(ORIGINAL, data)
    assert load(out) == data
    for kept in ("# 客服 agent", "# 改成你登记的 Provider", "# 越低越稳定", "# push 时上传", "# 查订单"):
        assert kept in out
    assert "temperature: 0.7" in out and "max_steps: 4" in out
    assert "0.3" not in out


def test_unchanged_data_round_trips_byte_for_byte():
    assert dump_preserving(ORIGINAL, load(ORIGINAL)) == ORIGINAL


def test_add_remove_and_list_changes():
    data = load(ORIGINAL)
    del data["runtime"]
    data["tools"] = ["file:tools/query-order.yaml", "file:tools/refund.yaml"]
    data["knowledge"] = {"inject": ["file:kb/faq.md"]}
    out = dump_preserving(ORIGINAL, data)
    assert load(out) == data
    assert "runtime" not in out and "# 查订单" in out and "# 越低越稳定" in out
    # 列表缩短
    data["tools"] = []
    assert load(dump_preserving(ORIGINAL, data)) == data


def test_indent_style_of_the_original_is_kept():
    flush = "tools:\n- a\n- b\nname: x   # n\n"  # 序列不缩进
    data = load(flush)
    data["tools"].append("c")
    out = dump_preserving(flush, data)
    assert out == "tools:\n- a\n- b\n- c\nname: x   # n\n"
    indented = "tools:\n    - a\nname: x\n"  # 序列缩进 4
    data = load(indented)
    data["tools"].append("b")
    assert dump_preserving(indented, data) == "tools:\n    - a\n    - b\nname: x\n"


def test_key_order_follows_new_data_and_unicode_is_kept():
    text = "b: 1  # B\na: 你好  # A\n"
    out = dump_preserving(text, {"a": "你好吗", "b": 1})
    assert out.index("a:") < out.index("b:") and "你好吗" in out
    assert load(out) == {"a": "你好吗", "b": 1}


def test_type_changes_and_nulls():
    text = "a: 1  # c\nb: [1, 2]\nc: {x: 1}\n"
    data = {"a": "str", "b": {"k": None}, "c": [1]}
    assert load(dump_preserving(text, data)) == data


def test_fallback_to_plain_dump_when_original_is_unusable():
    data = {"a": 1, "b": ["x"]}
    assert dump_preserving("a: [unclosed\n", data) == plain_dump(data)
    assert dump_preserving("- just\n- a list\n", data) == plain_dump(data)
    assert dump_preserving("", data) == plain_dump(data)


def test_merge_patch_rfc7396():
    target = {"a": "b", "c": {"d": "e", "f": "g"}, "list": [1, 2]}
    patch = {"a": "z", "c": {"f": None}, "list": [3], "new": {"x": 1}}
    assert merge_patch(target, patch) == {"a": "z", "c": {"d": "e"}, "list": [3], "new": {"x": 1}}
    assert target["c"]["f"] == "g"  # 不修改原对象
    assert merge_patch({"a": 1}, {"a": {"b": 1}}) == {"a": {"b": 1}}  # 标量被对象替换
    assert merge_patch({"a": {"b": 1}}, {"a": 5}) == {"a": 5}
    assert merge_patch({"a": 1}, {}) == {"a": 1}
    assert merge_patch("x", {"a": 1}) == {"a": 1}
    assert merge_patch({"a": 1}, [1, 2]) == [1, 2]
