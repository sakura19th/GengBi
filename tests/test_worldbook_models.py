"""全局世界书数据模型测试。

覆盖：
1. WorldBook 默认值
2. 空 entries 列表
3. 序列化 round-trip（含 by_alias=True 处理 _raw_st_fields 别名）
4. datetime 字段（created_at / updated_at，可为 None）
5. 嵌套 ContextEntry 列表
6. raw_st_fields ↔ _raw_st_fields 别名双向处理
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置离屏平台，避免在 CI 环境中需要显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from pydantic import ValidationError

from novelforge.models.context import ContextEntry
from novelforge.models.worldbook import WorldBook


# ===== 1. WorldBook 默认值 =====


def test_worldbook_defaults() -> None:
    """WorldBook 默认值实例化（id 与 name 必填）。"""
    wb = WorldBook(id="wb_1", name="默认世界书")
    assert wb.id == "wb_1"
    assert wb.name == "默认世界书"
    assert wb.entries == []
    assert wb.enabled is True
    # datetime 字段默认为 None（未持久化时）
    assert wb.created_at is None
    assert wb.updated_at is None
    assert wb.raw_st_fields == {}


def test_worldbook_requires_id_and_name() -> None:
    """WorldBook 必须提供 id 与 name。"""
    with pytest.raises(ValidationError):
        WorldBook(id="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        WorldBook(name="x")  # type: ignore[call-arg]


def test_worldbook_full_construction() -> None:
    """WorldBook 完整字段构造。"""
    now = datetime(2026, 6, 27, 12, 0, 0)
    wb = WorldBook(
        id="wb_full",
        name="完整世界书",
        entries=[
            ContextEntry(uid="e1", category="characters", content="主角"),
            ContextEntry(uid="e2", category="locations", content="京城"),
        ],
        enabled=False,
        created_at=now,
        updated_at=now,
        raw_st_fields={"unknown_top": "v"},
    )
    assert wb.id == "wb_full"
    assert wb.name == "完整世界书"
    assert len(wb.entries) == 2
    assert isinstance(wb.entries[0], ContextEntry)
    assert wb.entries[0].uid == "e1"
    assert wb.entries[0].category == "characters"
    assert wb.entries[1].uid == "e2"
    assert wb.enabled is False
    assert wb.created_at == now
    assert wb.updated_at == now
    assert wb.raw_st_fields == {"unknown_top": "v"}


# ===== 2. 空 entries 列表 =====


def test_worldbook_empty_entries() -> None:
    """WorldBook 可构造空 entries 列表。"""
    wb = WorldBook(id="wb_empty", name="空世界书", entries=[])
    assert wb.entries == []


def test_worldbook_default_entries_independent() -> None:
    """多个 WorldBook 实例的 entries 列表互不影响（default_factory 隔离）。"""
    wb1 = WorldBook(id="wb1", name="wb1")
    wb2 = WorldBook(id="wb2", name="wb2")
    wb1.entries.append(ContextEntry(uid="e1"))
    # wb2 不受影响
    assert wb2.entries == []


# ===== 3. datetime 字段（created_at / updated_at） =====


def test_worldbook_datetime_defaults_none() -> None:
    """WorldBook datetime 字段默认为 None。"""
    wb = WorldBook(id="wb", name="wb")
    assert wb.created_at is None
    assert wb.updated_at is None


def test_worldbook_datetime_custom() -> None:
    """WorldBook datetime 字段支持自定义时间。"""
    created = datetime(2026, 1, 1, 0, 0, 0)
    updated = datetime(2026, 6, 27, 15, 30, 0)
    wb = WorldBook(id="wb", name="wb", created_at=created, updated_at=updated)
    assert wb.created_at == created
    assert wb.updated_at == updated


def test_worldbook_datetime_roundtrip() -> None:
    """WorldBook datetime 字段 round-trip 后仍为 datetime 类型。"""
    now = datetime(2026, 6, 27, 12, 30, 45)
    wb = WorldBook(id="wb", name="wb", created_at=now, updated_at=now)
    restored = WorldBook.model_validate_json(wb.model_dump_json())
    assert isinstance(restored.created_at, datetime)
    assert isinstance(restored.updated_at, datetime)
    assert restored.created_at == now
    assert restored.updated_at == now


def test_worldbook_datetime_none_roundtrip() -> None:
    """WorldBook datetime 为 None 时 round-trip 保持 None。"""
    wb = WorldBook(id="wb", name="wb")
    restored = WorldBook.model_validate_json(wb.model_dump_json())
    assert restored.created_at is None
    assert restored.updated_at is None


# ===== 4. 别名处理 raw_st_fields ↔ _raw_st_fields =====


def test_worldbook_raw_st_fields_alias_serialization() -> None:
    """WorldBook 序列化时 by_alias=True 将 raw_st_fields 写为 _raw_st_fields。"""
    wb = WorldBook(id="wb", name="wb", raw_st_fields={"st_top": "v"})
    # by_alias=True
    dumped_alias = wb.model_dump(by_alias=True)
    assert "_raw_st_fields" in dumped_alias
    assert dumped_alias["_raw_st_fields"] == {"st_top": "v"}
    assert "raw_st_fields" not in dumped_alias
    # by_alias=False
    dumped_no_alias = wb.model_dump(by_alias=False)
    assert "raw_st_fields" in dumped_no_alias
    assert dumped_no_alias["raw_st_fields"] == {"st_top": "v"}
    assert "_raw_st_fields" not in dumped_no_alias


def test_worldbook_raw_st_fields_alias_deserialization() -> None:
    """WorldBook 可从 _raw_st_fields 别名或 raw_st_fields 字段名反序列化。"""
    # 从别名构造
    wb_from_alias = WorldBook.model_validate({
        "id": "wb",
        "name": "wb",
        "_raw_st_fields": {"st_only": True},
    })
    assert wb_from_alias.raw_st_fields == {"st_only": True}

    # 从字段名构造
    wb_from_name = WorldBook.model_validate({
        "id": "wb",
        "name": "wb",
        "raw_st_fields": {"name_only": True},
    })
    assert wb_from_name.raw_st_fields == {"name_only": True}


# ===== 5. 序列化 round-trip（含 by_alias=True） =====


def test_worldbook_roundtrip() -> None:
    """WorldBook（含嵌套 ContextEntry）序列化 round-trip。"""
    wb = WorldBook(
        id="wb_rt",
        name="round-trip 世界书",
        entries=[
            ContextEntry(
                uid="e1",
                category="characters",
                key=["主角"],
                content="主角信息",
                position="before",
            ),
            ContextEntry(
                uid="e2",
                category="locations",
                key=["京城"],
                content="京城信息",
                position="after",
            ),
        ],
        enabled=False,
        created_at=datetime(2026, 6, 27, 10, 0, 0),
        updated_at=datetime(2026, 6, 27, 11, 0, 0),
        raw_st_fields={"top_unknown": "top"},
    )
    json_str = wb.model_dump_json()
    restored = WorldBook.model_validate_json(json_str)
    assert restored.id == "wb_rt"
    assert restored.name == "round-trip 世界书"
    assert len(restored.entries) == 2
    assert isinstance(restored.entries[0], ContextEntry)
    assert restored.entries[0].uid == "e1"
    assert restored.entries[0].category == "characters"
    assert restored.entries[0].position == "before"
    assert restored.entries[0].key == ["主角"]
    assert restored.entries[1].uid == "e2"
    assert restored.entries[1].position == "after"
    assert restored.enabled is False
    assert restored.created_at == datetime(2026, 6, 27, 10, 0, 0)
    assert restored.updated_at == datetime(2026, 6, 27, 11, 0, 0)
    assert restored.raw_st_fields == {"top_unknown": "top"}


def test_worldbook_roundtrip_by_alias() -> None:
    """WorldBook 以 by_alias=True 序列化后仍可反序列化（ST 兼容场景）。"""
    wb = WorldBook(
        id="wb_alias",
        name="别名世界书",
        entries=[
            ContextEntry(uid="e1", raw_st_fields={"entry_st": "v"}),
        ],
        raw_st_fields={"top": "v"},
    )
    json_str = wb.model_dump_json(by_alias=True)
    data = json.loads(json_str)
    # 顶层与嵌套 ContextEntry 均应使用 _raw_st_fields 别名
    assert data["_raw_st_fields"] == {"top": "v"}
    assert data["entries"][0]["_raw_st_fields"] == {"entry_st": "v"}
    # 反序列化
    restored = WorldBook.model_validate_json(json_str)
    assert restored.raw_st_fields == {"top": "v"}
    assert restored.entries[0].raw_st_fields == {"entry_st": "v"}


def test_worldbook_from_dict_partial() -> None:
    """WorldBook 可从部分 dict 构造，缺失字段使用默认值。"""
    wb = WorldBook.model_validate({"id": "wb_dict", "name": "字典世界书"})
    assert wb.id == "wb_dict"
    assert wb.name == "字典世界书"
    assert wb.entries == []
    assert wb.enabled is True
    assert wb.created_at is None
    assert wb.updated_at is None


# ===== 6. 嵌套 ContextEntry field_validator 透传 =====


def test_worldbook_nested_context_entry_validators() -> None:
    """WorldBook 嵌套 ContextEntry 时，ContextEntry 的 field_validator 仍生效。"""
    # ContextEntry 的 category 非法时应抛 ValidationError
    with pytest.raises(ValidationError):
        WorldBook.model_validate({
            "id": "wb",
            "name": "wb",
            "entries": [
                {"uid": "e1", "category": "invalid_category"},
            ],
        })

    # ContextEntry 的 position 非法时应抛 ValidationError
    with pytest.raises(ValidationError):
        WorldBook.model_validate({
            "id": "wb",
            "name": "wb",
            "entries": [
                {"uid": "e1", "position": "invalid_position"},
            ],
        })


def test_worldbook_nested_context_entry_with_source_chapter_range() -> None:
    """WorldBook 嵌套 ContextEntry 含 source_chapter_range tuple round-trip。"""
    wb = WorldBook(
        id="wb_range",
        name="range 世界书",
        entries=[
            ContextEntry(
                uid="e1",
                content="条目",
                source_chapter_range=(0, 4),
            ),
        ],
    )
    json_str = wb.model_dump_json()
    restored = WorldBook.model_validate_json(json_str)
    assert len(restored.entries) == 1
    # tuple 经 JSON 序列化为 list，反序列化后应为 tuple
    assert restored.entries[0].source_chapter_range == (0, 4)
