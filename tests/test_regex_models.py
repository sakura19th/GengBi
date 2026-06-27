"""正则脚本数据模型测试。

覆盖：
1. RegexScript 默认值
2. placement field_validator 校验（仅允许 VALID_PLACEMENTS: 1/2/5）
3. substituteRegex 作为 bool 与 int
4. 空 placement 列表
5. 序列化 round-trip（含 by_alias=True 处理 _raw_st_fields 别名）
6. raw_st_fields ↔ _raw_st_fields 别名双向处理
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置离屏平台，避免在 CI 环境中需要显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from pydantic import ValidationError

from novelforge.models.regex import (
    PLACEMENT_AI_OUTPUT,
    PLACEMENT_USER_INPUT,
    PLACEMENT_WORLD_INFO,
    VALID_PLACEMENTS,
    RegexScript,
)


# ===== 1. RegexScript 默认值 =====


def test_regex_script_defaults() -> None:
    """RegexScript 默认值实例化（id 必填）。"""
    r = RegexScript(id="rx_1")
    assert r.id == "rx_1"
    assert r.scriptName == ""
    assert r.findRegex == ""
    assert r.replaceString == ""
    assert r.trimStrings == []
    assert r.placement == []
    assert r.disabled is False
    assert r.markdownOnly is False
    assert r.promptOnly is False
    assert r.runOnEdit is False
    assert r.substituteRegex is False
    assert r.minDepth == 0
    assert r.maxDepth == 0
    assert r.markupSafety is False
    assert r.raw_st_fields == {}


def test_regex_script_requires_id() -> None:
    """RegexScript 必须提供 id 字段。"""
    with pytest.raises(ValidationError):
        RegexScript()  # type: ignore[call-arg]


def test_regex_script_full_construction() -> None:
    """RegexScript 完整字段构造。"""
    r = RegexScript(
        id="rx_full",
        scriptName="替换脚本",
        findRegex=r"/\s+/g",
        replaceString=" ",
        trimStrings=["\n", "\t"],
        placement=[PLACEMENT_USER_INPUT, PLACEMENT_AI_OUTPUT],
        disabled=True,
        markdownOnly=True,
        promptOnly=True,
        runOnEdit=True,
        substituteRegex=True,
        minDepth=1,
        maxDepth=5,
        markupSafety=True,
    )
    assert r.scriptName == "替换脚本"
    assert r.findRegex == r"/\s+/g"
    assert r.replaceString == " "
    assert r.trimStrings == ["\n", "\t"]
    assert r.placement == [PLACEMENT_USER_INPUT, PLACEMENT_AI_OUTPUT]
    assert r.disabled is True
    assert r.markdownOnly is True
    assert r.promptOnly is True
    assert r.runOnEdit is True
    assert r.substituteRegex is True
    assert r.minDepth == 1
    assert r.maxDepth == 5
    assert r.markupSafety is True


def test_regex_script_default_lists_independent() -> None:
    """多个 RegexScript 实例的 list 字段互不影响（default_factory 隔离）。"""
    r1 = RegexScript(id="r1")
    r2 = RegexScript(id="r2")
    r1.placement.append(1)
    r1.trimStrings.append("x")
    # r2 不受影响
    assert r2.placement == []
    assert r2.trimStrings == []


# ===== 2. placement field_validator 校验 =====


def test_placement_valid_values() -> None:
    """placement 接受 VALID_PLACEMENTS 中的值（1/2/5）。"""
    assert VALID_PLACEMENTS == frozenset({1, 2, 5})
    assert PLACEMENT_USER_INPUT == 1
    assert PLACEMENT_AI_OUTPUT == 2
    assert PLACEMENT_WORLD_INFO == 5

    r = RegexScript(id="rx", placement=[1, 2, 5])
    assert r.placement == [1, 2, 5]


def test_placement_rejects_invalid_value() -> None:
    """placement 拒绝不在 VALID_PLACEMENTS 中的值。"""
    with pytest.raises(ValidationError):
        RegexScript(id="rx", placement=[0])
    with pytest.raises(ValidationError):
        RegexScript(id="rx", placement=[3])
    with pytest.raises(ValidationError):
        RegexScript(id="rx", placement=[4])
    with pytest.raises(ValidationError):
        RegexScript(id="rx", placement=[6])
    with pytest.raises(ValidationError):
        RegexScript(id="rx", placement=[100])


def test_placement_rejects_invalid_in_mixed_list() -> None:
    """placement 列表中混入非法值时拒绝。"""
    with pytest.raises(ValidationError):
        RegexScript(id="rx", placement=[1, 2, 99])


def test_placement_empty_list_allowed() -> None:
    """placement 空列表允许（表示不应用到任何时机）。"""
    r = RegexScript(id="rx", placement=[])
    assert r.placement == []


def test_placement_allows_duplicates() -> None:
    """placement 允许重复值（validator 仅校验取值合法性）。"""
    r = RegexScript(id="rx", placement=[1, 1, 2, 2])
    assert r.placement == [1, 1, 2, 2]


# ===== 3. substituteRegex 作为 bool 与 int =====


def test_substitute_regex_as_bool() -> None:
    """substituteRegex 接受 bool 值。"""
    r_true = RegexScript(id="rx", substituteRegex=True)
    assert r_true.substituteRegex is True
    r_false = RegexScript(id="rx", substituteRegex=False)
    assert r_false.substituteRegex is False


def test_substitute_regex_as_int() -> None:
    """substituteRegex 接受 int 值（ST 用 0/1 表示）。"""
    r0 = RegexScript(id="rx", substituteRegex=0)
    assert r0.substituteRegex == 0
    r1 = RegexScript(id="rx", substituteRegex=1)
    assert r1.substituteRegex == 1


def test_substitute_regex_roundtrip_preserves_type() -> None:
    """substituteRegex 序列化/反序列化保留原始类型（bool 与 int 分开测试）。"""
    # bool 类型 round-trip
    r_bool = RegexScript(id="rx", substituteRegex=True)
    restored_bool = RegexScript.model_validate_json(r_bool.model_dump_json())
    assert restored_bool.substituteRegex is True

    # int 类型 round-trip
    r_int = RegexScript(id="rx", substituteRegex=1)
    restored_int = RegexScript.model_validate_json(r_int.model_dump_json())
    assert restored_int.substituteRegex == 1


# ===== 4. 别名处理 raw_st_fields ↔ _raw_st_fields =====


def test_regex_raw_st_fields_alias_serialization() -> None:
    """RegexScript 序列化时 by_alias=True 将 raw_st_fields 写为 _raw_st_fields。"""
    r = RegexScript(id="rx", raw_st_fields={"unknown_st_field": "v"})
    # by_alias=True
    dumped_alias = r.model_dump(by_alias=True)
    assert "_raw_st_fields" in dumped_alias
    assert dumped_alias["_raw_st_fields"] == {"unknown_st_field": "v"}
    assert "raw_st_fields" not in dumped_alias
    # by_alias=False
    dumped_no_alias = r.model_dump(by_alias=False)
    assert "raw_st_fields" in dumped_no_alias
    assert dumped_no_alias["raw_st_fields"] == {"unknown_st_field": "v"}
    assert "_raw_st_fields" not in dumped_no_alias


def test_regex_raw_st_fields_alias_deserialization() -> None:
    """RegexScript 可从 _raw_st_fields 别名或 raw_st_fields 字段名反序列化。"""
    # 从别名构造
    r_from_alias = RegexScript.model_validate({
        "id": "rx",
        "_raw_st_fields": {"st_only": True},
    })
    assert r_from_alias.raw_st_fields == {"st_only": True}

    # 从字段名构造
    r_from_name = RegexScript.model_validate({
        "id": "rx",
        "raw_st_fields": {"name_only": True},
    })
    assert r_from_name.raw_st_fields == {"name_only": True}


# ===== 5. 序列化 round-trip =====


def test_regex_script_roundtrip() -> None:
    """RegexScript 序列化/反序列化 round-trip。"""
    r = RegexScript(
        id="rx_rt",
        scriptName="round-trip 脚本",
        findRegex=r"/\s+/g",
        replaceString=" ",
        trimStrings=["\n"],
        placement=[PLACEMENT_USER_INPUT, PLACEMENT_AI_OUTPUT, PLACEMENT_WORLD_INFO],
        disabled=True,
        substituteRegex=True,
        minDepth=2,
        maxDepth=4,
        raw_st_fields={"unknown": "field"},
    )
    json_str = r.model_dump_json()
    restored = RegexScript.model_validate_json(json_str)
    assert restored.id == "rx_rt"
    assert restored.scriptName == "round-trip 脚本"
    assert restored.findRegex == r"/\s+/g"
    assert restored.replaceString == " "
    assert restored.trimStrings == ["\n"]
    assert restored.placement == [1, 2, 5]
    assert restored.disabled is True
    assert restored.substituteRegex is True
    assert restored.minDepth == 2
    assert restored.maxDepth == 4
    assert restored.raw_st_fields == {"unknown": "field"}


def test_regex_script_roundtrip_by_alias() -> None:
    """RegexScript 以 by_alias=True 序列化后仍可反序列化（ST 兼容场景）。"""
    r = RegexScript(
        id="rx_alias",
        scriptName="别名脚本",
        placement=[PLACEMENT_USER_INPUT],
        raw_st_fields={"st_field": "v"},
    )
    json_str = r.model_dump_json(by_alias=True)
    data = json.loads(json_str)
    assert "_raw_st_fields" in data
    assert data["_raw_st_fields"] == {"st_field": "v"}
    # 反序列化
    restored = RegexScript.model_validate_json(json_str)
    assert restored.id == "rx_alias"
    assert restored.scriptName == "别名脚本"
    assert restored.placement == [PLACEMENT_USER_INPUT]
    assert restored.raw_st_fields == {"st_field": "v"}


def test_regex_script_from_dict_partial() -> None:
    """RegexScript 可从部分 dict 构造，缺失字段使用默认值。"""
    r = RegexScript.model_validate({"id": "rx_dict", "scriptName": "字典脚本"})
    assert r.id == "rx_dict"
    assert r.scriptName == "字典脚本"
    assert r.placement == []
    assert r.substituteRegex is False


def test_regex_script_empty_placement_roundtrip() -> None:
    """RegexScript 空 placement 列表 round-trip 保持空。"""
    r = RegexScript(id="rx", placement=[])
    restored = RegexScript.model_validate_json(r.model_dump_json())
    assert restored.placement == []
