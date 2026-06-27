"""写作预设数据模型测试。

覆盖：
1. Prompt / PromptOrderEntry / PromptOrderGroup / WritingPreset 默认值
2. Prompt.injection_position field_validator 校验（仅允许 0/1）
3. WritingPreset 空 prompts 列表
4. 序列化 round-trip（含 by_alias=True 处理 _raw_st_fields 别名）
5. PromptOrderEntry / PromptOrderGroup model_config populate_by_name
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

from novelforge.models.preset import (
    GLOBAL_CHARACTER_ID,
    Prompt,
    PromptOrderEntry,
    PromptOrderGroup,
    WritingPreset,
)


# ===== 1. Prompt 默认值 =====


def test_prompt_defaults() -> None:
    """Prompt 默认值实例化（identifier 必填）。"""
    p = Prompt(identifier="main")
    assert p.identifier == "main"
    assert p.name == ""
    assert p.role == "system"
    assert p.content == ""
    assert p.system_prompt is False
    assert p.marker is None
    assert p.position == "start"
    assert p.injection_position == 0
    assert p.injection_depth == 4
    assert p.injection_order == 100
    assert p.forbid_overrides is False
    assert p.extension == {}
    assert p.enabled is True
    assert p.raw_st_fields == {}


def test_prompt_requires_identifier() -> None:
    """Prompt 必须提供 identifier 字段。"""
    with pytest.raises(ValidationError):
        Prompt()  # type: ignore[call-arg]


def test_prompt_custom_values() -> None:
    """Prompt 支持完整自定义字段。"""
    p = Prompt(
        identifier="chatHistory",
        name="聊天历史",
        role="user",
        content="内容",
        system_prompt=True,
        marker="chatHistory",
        position="end",
        injection_position=1,
        injection_depth=2,
        injection_order=200,
        forbid_overrides=True,
        extension={"ext": "val"},
        enabled=False,
    )
    assert p.identifier == "chatHistory"
    assert p.role == "user"
    assert p.system_prompt is True
    assert p.marker == "chatHistory"
    assert p.position == "end"
    assert p.injection_position == 1
    assert p.injection_depth == 2
    assert p.injection_order == 200
    assert p.forbid_overrides is True
    assert p.extension == {"ext": "val"}
    assert p.enabled is False


# ===== 2. Prompt.injection_position field_validator 校验 =====


def test_prompt_injection_position_valid() -> None:
    """injection_position 接受 0 和 1。"""
    p0 = Prompt(identifier="p0", injection_position=0)
    assert p0.injection_position == 0
    p1 = Prompt(identifier="p1", injection_position=1)
    assert p1.injection_position == 1


def test_prompt_injection_position_rejects_invalid_int() -> None:
    """injection_position 拒绝非 0/1 的整数。"""
    with pytest.raises(ValidationError):
        Prompt(identifier="bad", injection_position=2)
    with pytest.raises(ValidationError):
        Prompt(identifier="bad", injection_position=-1)
    with pytest.raises(ValidationError):
        Prompt(identifier="bad", injection_position=3)


def test_prompt_injection_position_rejects_non_integer() -> None:
    """injection_position 拒绝无法转为整数的字符串（类型校验）。

    注意：pydantic 会将 "0"/"1" 这样的数字字符串强制转换为 int，
    因此仅无法解析的字符串才会触发 ValidationError。
    """
    with pytest.raises(ValidationError):
        Prompt(identifier="bad", injection_position="abc")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Prompt(identifier="bad", injection_position=[0])  # type: ignore[arg-type]


# ===== 3. PromptOrderEntry / PromptOrderGroup =====


def test_prompt_order_entry_defaults() -> None:
    """PromptOrderEntry 默认值实例化（identifier 必填）。"""
    entry = PromptOrderEntry(identifier="main")
    assert entry.identifier == "main"
    assert entry.enabled is True


def test_prompt_order_entry_requires_identifier() -> None:
    """PromptOrderEntry 必须提供 identifier。"""
    with pytest.raises(ValidationError):
        PromptOrderEntry()  # type: ignore[call-arg]


def test_prompt_order_group_defaults() -> None:
    """PromptOrderGroup 默认值实例化。"""
    group = PromptOrderGroup()
    assert group.character_id == GLOBAL_CHARACTER_ID
    assert group.character_id == 100000
    assert group.order == []


def test_prompt_order_group_with_entries() -> None:
    """PromptOrderGroup 嵌套 entries 列表。"""
    group = PromptOrderGroup(
        character_id=100001,
        order=[
            PromptOrderEntry(identifier="main", enabled=True),
            PromptOrderEntry(identifier="chatHistory", enabled=False),
        ],
    )
    assert group.character_id == 100001
    assert len(group.order) == 2
    assert group.order[0].identifier == "main"
    assert group.order[1].enabled is False


def test_prompt_order_group_default_order_independent() -> None:
    """多个 PromptOrderGroup 实例的 order 列表互不影响。"""
    g1 = PromptOrderGroup()
    g2 = PromptOrderGroup()
    g1.order.append(PromptOrderEntry(identifier="x"))
    assert g2.order == []


def test_prompt_order_group_model_config_populate_by_name() -> None:
    """PromptOrderGroup populate_by_name=True：可从 dict 构造。"""
    group = PromptOrderGroup.model_validate({
        "character_id": 100002,
        "order": [{"identifier": "main", "enabled": True}],
    })
    assert group.character_id == 100002
    assert len(group.order) == 1
    assert isinstance(group.order[0], PromptOrderEntry)
    assert group.order[0].identifier == "main"


# ===== 4. WritingPreset 默认值与空 prompts =====


def test_writing_preset_defaults() -> None:
    """WritingPreset 默认值实例化（id 与 name 必填）。"""
    wp = WritingPreset(id="preset_1", name="默认预设")
    assert wp.id == "preset_1"
    assert wp.name == "默认预设"
    assert wp.prompts == []
    assert wp.prompt_order == []
    assert wp.generation_params == {}
    assert wp.enabled is True
    assert wp.raw_st_fields == {}


def test_writing_preset_requires_id_and_name() -> None:
    """WritingPreset 必须提供 id 与 name。"""
    with pytest.raises(ValidationError):
        WritingPreset(id="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        WritingPreset(name="x")  # type: ignore[call-arg]


def test_writing_preset_empty_prompts() -> None:
    """WritingPreset 可构造空 prompts 列表。"""
    wp = WritingPreset(id="p_empty", name="空预设", prompts=[])
    assert wp.prompts == []
    assert wp.prompt_order == []


def test_writing_preset_with_prompts_and_order() -> None:
    """WritingPreset 含 prompts 与 prompt_order 完整构造。"""
    wp = WritingPreset(
        id="p_full",
        name="完整预设",
        prompts=[
            Prompt(identifier="main", system_prompt=True),
            Prompt(identifier="chatHistory", marker="chatHistory"),
        ],
        prompt_order=[
            PromptOrderGroup(
                character_id=GLOBAL_CHARACTER_ID,
                order=[
                    PromptOrderEntry(identifier="main"),
                    PromptOrderEntry(identifier="chatHistory", enabled=False),
                ],
            ),
        ],
        generation_params={"temperature": 0.8, "max_tokens": 1024},
        enabled=False,
    )
    assert len(wp.prompts) == 2
    assert wp.prompts[0].identifier == "main"
    assert wp.prompts[0].system_prompt is True
    assert wp.prompts[1].marker == "chatHistory"
    assert len(wp.prompt_order) == 1
    assert wp.prompt_order[0].character_id == GLOBAL_CHARACTER_ID
    assert len(wp.prompt_order[0].order) == 2
    assert wp.generation_params == {"temperature": 0.8, "max_tokens": 1024}
    assert wp.enabled is False


# ===== 5. 别名处理 raw_st_fields ↔ _raw_st_fields =====


def test_prompt_raw_st_fields_alias_serialization() -> None:
    """Prompt 序列化时 by_alias=True 将 raw_st_fields 写为 _raw_st_fields。"""
    p = Prompt(identifier="p1", raw_st_fields={"unknown_field": "value"})
    # by_alias=True：输出 _raw_st_fields
    dumped_alias = p.model_dump(by_alias=True)
    assert "_raw_st_fields" in dumped_alias
    assert dumped_alias["_raw_st_fields"] == {"unknown_field": "value"}
    assert "raw_st_fields" not in dumped_alias
    # by_alias=False：输出 raw_st_fields
    dumped_no_alias = p.model_dump(by_alias=False)
    assert "raw_st_fields" in dumped_no_alias
    assert dumped_no_alias["raw_st_fields"] == {"unknown_field": "value"}
    assert "_raw_st_fields" not in dumped_no_alias


def test_prompt_raw_st_fields_alias_deserialization() -> None:
    """Prompt 可从 _raw_st_fields 别名或 raw_st_fields 字段名反序列化。"""
    # 从别名 _raw_st_fields 构造（ST 导入场景）
    p_from_alias = Prompt.model_validate({
        "identifier": "p1",
        "_raw_st_fields": {"st_only": True},
    })
    assert p_from_alias.raw_st_fields == {"st_only": True}

    # 从字段名 raw_st_fields 构造
    p_from_name = Prompt.model_validate({
        "identifier": "p2",
        "raw_st_fields": {"name_only": True},
    })
    assert p_from_name.raw_st_fields == {"name_only": True}


def test_writing_preset_raw_st_fields_alias() -> None:
    """WritingPreset 的 raw_st_fields 别名处理。"""
    wp = WritingPreset(
        id="p1",
        name="预设",
        raw_st_fields={"st_top": "top_val"},
    )
    dumped = wp.model_dump(by_alias=True)
    assert "_raw_st_fields" in dumped
    assert dumped["_raw_st_fields"] == {"st_top": "top_val"}
    # 反序列化
    restored = WritingPreset.model_validate(dumped)
    assert restored.raw_st_fields == {"st_top": "top_val"}


# ===== 6. 序列化 round-trip（含 by_alias=True） =====


def test_prompt_roundtrip() -> None:
    """Prompt 序列化/反序列化 round-trip。"""
    p = Prompt(
        identifier="main",
        name="主提示",
        role="system",
        content="系统内容",
        injection_position=1,
        injection_depth=3,
        raw_st_fields={"unknown": "field"},
    )
    # 默认 model_dump_json 不使用别名，需手动验证两种方式 round-trip
    json_str = p.model_dump_json()
    restored = Prompt.model_validate_json(json_str)
    assert restored.identifier == "main"
    assert restored.name == "主提示"
    assert restored.role == "system"
    assert restored.content == "系统内容"
    assert restored.injection_position == 1
    assert restored.injection_depth == 3
    assert restored.raw_st_fields == {"unknown": "field"}


def test_prompt_roundtrip_by_alias() -> None:
    """Prompt 以 by_alias=True 序列化后仍可反序列化（ST 兼容场景）。"""
    p = Prompt(
        identifier="main",
        raw_st_fields={"st_field": "v"},
    )
    # by_alias=True 序列化（输出 _raw_st_fields）
    json_str = p.model_dump_json(by_alias=True)
    data = json.loads(json_str)
    assert "_raw_st_fields" in data
    # 反序列化回 Prompt
    restored = Prompt.model_validate_json(json_str)
    assert restored.identifier == "main"
    assert restored.raw_st_fields == {"st_field": "v"}


def test_writing_preset_roundtrip() -> None:
    """WritingPreset（含嵌套 Prompt 与 PromptOrderGroup）序列化 round-trip。"""
    wp = WritingPreset(
        id="p_rt",
        name="round-trip 预设",
        prompts=[
            Prompt(identifier="main", system_prompt=True, content="系统"),
            Prompt(
                identifier="chatHistory",
                marker="chatHistory",
                injection_position=1,
                injection_depth=2,
                raw_st_fields={"extra": "x"},
            ),
        ],
        prompt_order=[
            PromptOrderGroup(
                character_id=GLOBAL_CHARACTER_ID,
                order=[
                    PromptOrderEntry(identifier="main"),
                    PromptOrderEntry(identifier="chatHistory", enabled=False),
                ],
            ),
        ],
        generation_params={"temperature": 0.7, "max_tokens": 2048},
        raw_st_fields={"top_unknown": "top"},
    )
    json_str = wp.model_dump_json()
    restored = WritingPreset.model_validate_json(json_str)
    assert restored.id == "p_rt"
    assert restored.name == "round-trip 预设"
    assert len(restored.prompts) == 2
    assert restored.prompts[0].identifier == "main"
    assert restored.prompts[0].system_prompt is True
    assert restored.prompts[1].marker == "chatHistory"
    assert restored.prompts[1].injection_position == 1
    assert restored.prompts[1].raw_st_fields == {"extra": "x"}
    assert len(restored.prompt_order) == 1
    assert isinstance(restored.prompt_order[0], PromptOrderGroup)
    assert restored.prompt_order[0].character_id == GLOBAL_CHARACTER_ID
    assert len(restored.prompt_order[0].order) == 2
    assert isinstance(restored.prompt_order[0].order[0], PromptOrderEntry)
    assert restored.generation_params == {"temperature": 0.7, "max_tokens": 2048}
    assert restored.raw_st_fields == {"top_unknown": "top"}


def test_writing_preset_roundtrip_by_alias() -> None:
    """WritingPreset 以 by_alias=True 序列化后仍可反序列化。"""
    wp = WritingPreset(
        id="p_alias",
        name="别名预设",
        prompts=[Prompt(identifier="main", raw_st_fields={"a": "b"})],
        raw_st_fields={"top": "v"},
    )
    json_str = wp.model_dump_json(by_alias=True)
    data = json.loads(json_str)
    # 顶层与嵌套均应使用 _raw_st_fields 别名
    assert data["_raw_st_fields"] == {"top": "v"}
    assert data["prompts"][0]["_raw_st_fields"] == {"a": "b"}
    # 反序列化
    restored = WritingPreset.model_validate_json(json_str)
    assert restored.raw_st_fields == {"top": "v"}
    assert restored.prompts[0].raw_st_fields == {"a": "b"}
