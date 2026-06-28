"""章节与续写版本数据模型测试。

覆盖：
1. Chapter / Continuation 默认值
2. Chapter 含多个 Continuation（嵌套序列化）
3. Continuation 含 preset_snapshot / prompt_snapshot / parameters_snapshot
4. 序列化 round-trip（model_dump_json → model_validate_json）
5. datetime 字段（created_at / updated_at）
6. source_chapter_range tuple 序列化（tuple → list in JSON → back）
"""
from __future__ import annotations

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

from novelforge.models.chapter import Chapter, Continuation
from novelforge.models.context import ContextEntry


# ===== 1. Continuation 默认值 =====


def test_continuation_defaults() -> None:
    """Continuation 默认值实例化（id 必填）。"""
    cont = Continuation(id="cont_1")
    assert cont.id == "cont_1"
    assert cont.content == ""
    assert cont.model == ""
    assert cont.is_accepted is False
    assert cont.status == "completed"
    assert cont.created_by == "continuation"
    assert cont.parameters_snapshot == {}
    assert cont.preset_id == ""
    assert cont.preset_snapshot == {}
    assert cont.regex_script_ids_snapshot == []
    assert cont.extracted_context_snapshot == []
    assert cont.prompt_snapshot == []
    assert cont.reasoning_content is None
    assert cont.agent_artifacts is None
    # datetime 字段应有默认值
    assert isinstance(cont.created_at, datetime)


def test_continuation_requires_id() -> None:
    """Continuation 必须提供 id 字段。"""
    with pytest.raises(ValidationError):
        Continuation()  # type: ignore[call-arg]


def test_continuation_full_construction() -> None:
    """Continuation 完整字段构造。"""
    cont = Continuation(
        id="cont_full",
        content="续写正文",
        model="gpt-4",
        is_accepted=True,
        status="completed",
        created_by="rewrite",
        parameters_snapshot={"temperature": 0.8, "max_tokens": 1024},
        preset_id="preset_1",
        preset_snapshot={"id": "preset_1", "name": "预设"},
        regex_script_ids_snapshot=["rx_1", "rx_2"],
        extracted_context_snapshot=[
            ContextEntry(uid="e1", content="上下文"),
        ],
        prompt_snapshot=[
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": "用户消息"},
        ],
        reasoning_content="推理内容",
    )
    assert cont.id == "cont_full"
    assert cont.content == "续写正文"
    assert cont.model == "gpt-4"
    assert cont.is_accepted is True
    assert cont.created_by == "rewrite"
    assert cont.parameters_snapshot == {"temperature": 0.8, "max_tokens": 1024}
    assert cont.preset_id == "preset_1"
    assert cont.preset_snapshot == {"id": "preset_1", "name": "预设"}
    assert cont.regex_script_ids_snapshot == ["rx_1", "rx_2"]
    assert len(cont.extracted_context_snapshot) == 1
    assert isinstance(cont.extracted_context_snapshot[0], ContextEntry)
    assert cont.extracted_context_snapshot[0].uid == "e1"
    assert len(cont.prompt_snapshot) == 2
    assert cont.prompt_snapshot[0]["role"] == "system"
    assert cont.reasoning_content == "推理内容"


def test_continuation_default_lists_independent() -> None:
    """多个 Continuation 实例的 list/dict 字段互不影响（default_factory 隔离）。"""
    c1 = Continuation(id="c1")
    c2 = Continuation(id="c2")
    c1.parameters_snapshot["k"] = "v"
    c1.regex_script_ids_snapshot.append("rx")
    c1.prompt_snapshot.append({"role": "user"})
    c1.extracted_context_snapshot.append(ContextEntry(uid="e1"))
    # c2 不受影响
    assert c2.parameters_snapshot == {}
    assert c2.regex_script_ids_snapshot == []
    assert c2.prompt_snapshot == []
    assert c2.extracted_context_snapshot == []


# ===== 2. Chapter 默认值 =====


def test_chapter_defaults() -> None:
    """Chapter 默认值实例化（id/project_id/index 必填）。"""
    ch = Chapter(id="ch_1", project_id="proj_1", index=0)
    assert ch.id == "ch_1"
    assert ch.project_id == "proj_1"
    assert ch.index == 0
    assert ch.title == ""
    assert ch.content == ""
    assert ch.word_count == 0
    assert ch.continuations == []
    assert ch.metadata == {}
    # datetime 字段应有默认值
    assert isinstance(ch.created_at, datetime)
    assert isinstance(ch.updated_at, datetime)


def test_chapter_requires_fields() -> None:
    """Chapter 必须提供 id / project_id / index。"""
    with pytest.raises(ValidationError):
        Chapter(id="x", index=0)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Chapter(project_id="x", index=0)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Chapter(id="x", project_id="x")  # type: ignore[call-arg]


def test_chapter_full_construction() -> None:
    """Chapter 完整字段构造。"""
    ch = Chapter(
        id="ch_full",
        project_id="proj_1",
        index=5,
        title="第五章",
        content="章节正文内容",
        word_count=3000,
        metadata={"notes": "备注", "tags": ["tag1", "tag2"]},
    )
    assert ch.id == "ch_full"
    assert ch.project_id == "proj_1"
    assert ch.index == 5
    assert ch.title == "第五章"
    assert ch.content == "章节正文内容"
    assert ch.word_count == 3000
    assert ch.metadata == {"notes": "备注", "tags": ["tag1", "tag2"]}


def test_chapter_default_fields_independent() -> None:
    """多个 Chapter 实例的 list/dict 字段互不影响（default_factory 隔离）。"""
    ch1 = Chapter(id="ch1", project_id="p", index=0)
    ch2 = Chapter(id="ch2", project_id="p", index=1)
    ch1.continuations.append(Continuation(id="c1"))
    ch1.metadata["k"] = "v"
    # ch2 不受影响
    assert ch2.continuations == []
    assert ch2.metadata == {}


# ===== 3. Chapter 含多个 Continuation（嵌套） =====


def test_chapter_with_multiple_continuations() -> None:
    """Chapter 含多个 Continuation 嵌套构造。"""
    cont1 = Continuation(id="c1", content="版本1", model="m1", is_accepted=True)
    cont2 = Continuation(id="c2", content="版本2", model="m2", is_accepted=False)
    cont3 = Continuation(id="c3", content="版本3", model="m3")
    ch = Chapter(
        id="ch_multi",
        project_id="proj_1",
        index=0,
        continuations=[cont1, cont2, cont3],
    )
    assert len(ch.continuations) == 3
    assert ch.continuations[0].id == "c1"
    assert ch.continuations[0].is_accepted is True
    assert ch.continuations[1].content == "版本2"
    assert ch.continuations[1].is_accepted is False
    assert ch.continuations[2].id == "c3"
    assert isinstance(ch.continuations[0], Continuation)


def test_chapter_with_continuation_agent_artifacts() -> None:
    """Chapter 含带 agent_artifacts 的 Continuation 嵌套构造（dict 类型，向后兼容）。"""
    cont = Continuation(
        id="c_agent",
        content="正文",
        model="m",
        agent_artifacts={"revision_rounds": 3},
    )
    ch = Chapter(
        id="ch_agent",
        project_id="proj",
        index=0,
        continuations=[cont],
    )
    assert len(ch.continuations) == 1
    assert ch.continuations[0].agent_artifacts is not None
    assert isinstance(ch.continuations[0].agent_artifacts, dict)
    assert ch.continuations[0].agent_artifacts["revision_rounds"] == 3


# ===== 4. Continuation 含 snapshot 字段 =====


def test_continuation_with_all_snapshots() -> None:
    """Continuation 含 preset_snapshot / prompt_snapshot / parameters_snapshot。"""
    cont = Continuation(
        id="c_snap",
        preset_id="preset_1",
        parameters_snapshot={
            "temperature": 0.9,
            "max_tokens": 2048,
            "top_p": 0.95,
            "frequency_penalty": 0.3,
        },
        preset_snapshot={
            "id": "preset_1",
            "name": "默认预设",
            "prompts": [{"identifier": "main"}],
        },
        regex_script_ids_snapshot=["rx_1", "rx_2", "rx_3"],
        extracted_context_snapshot=[
            ContextEntry(uid="e1", category="characters", content="人物"),
            ContextEntry(uid="e2", category="locations", content="地点"),
        ],
        prompt_snapshot=[
            {"role": "system", "content": "你是小说续写助手"},
            {"role": "user", "content": "请续写以下内容..."},
            {"role": "assistant", "content": "续写结果"},
        ],
    )
    assert cont.preset_id == "preset_1"
    assert cont.parameters_snapshot["temperature"] == 0.9
    assert cont.parameters_snapshot["max_tokens"] == 2048
    assert cont.preset_snapshot["name"] == "默认预设"
    assert len(cont.preset_snapshot["prompts"]) == 1
    assert cont.regex_script_ids_snapshot == ["rx_1", "rx_2", "rx_3"]
    assert len(cont.extracted_context_snapshot) == 2
    assert cont.extracted_context_snapshot[0].category == "characters"
    assert cont.extracted_context_snapshot[1].category == "locations"
    assert len(cont.prompt_snapshot) == 3
    assert cont.prompt_snapshot[0]["role"] == "system"
    assert cont.prompt_snapshot[2]["content"] == "续写结果"


# ===== 5. 序列化 round-trip =====


def test_continuation_roundtrip() -> None:
    """Continuation 序列化/反序列化 round-trip。"""
    cont = Continuation(
        id="c_rt",
        content="round-trip 正文",
        model="gpt-4",
        is_accepted=True,
        status="completed",
        created_by="rewrite",
        parameters_snapshot={"temperature": 0.7},
        preset_id="preset_rt",
        preset_snapshot={"id": "preset_rt", "name": "预设"},
        regex_script_ids_snapshot=["rx_1"],
        extracted_context_snapshot=[
            ContextEntry(uid="e1", content="上下文条目"),
        ],
        prompt_snapshot=[
            {"role": "system", "content": "系统"},
            {"role": "user", "content": "用户"},
        ],
        reasoning_content="推理过程",
    )
    json_str = cont.model_dump_json()
    restored = Continuation.model_validate_json(json_str)
    assert restored.id == "c_rt"
    assert restored.content == "round-trip 正文"
    assert restored.model == "gpt-4"
    assert restored.is_accepted is True
    assert restored.created_by == "rewrite"
    assert restored.parameters_snapshot == {"temperature": 0.7}
    assert restored.preset_id == "preset_rt"
    assert restored.preset_snapshot == {"id": "preset_rt", "name": "预设"}
    assert restored.regex_script_ids_snapshot == ["rx_1"]
    assert len(restored.extracted_context_snapshot) == 1
    assert isinstance(restored.extracted_context_snapshot[0], ContextEntry)
    assert restored.extracted_context_snapshot[0].uid == "e1"
    assert restored.extracted_context_snapshot[0].content == "上下文条目"
    assert restored.prompt_snapshot == [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "用户"},
    ]
    assert restored.reasoning_content == "推理过程"
    # datetime 字段 round-trip 后仍为 datetime
    assert isinstance(restored.created_at, datetime)


def test_chapter_roundtrip() -> None:
    """Chapter（含嵌套 Continuation）序列化/反序列化 round-trip。"""
    ch = Chapter(
        id="ch_rt",
        project_id="proj_rt",
        index=3,
        title="第三章",
        content="章节正文",
        word_count=1500,
        metadata={"notes": "章节备注", "tags": ["tag_a"]},
        continuations=[
            Continuation(
                id="c1",
                content="版本1",
                model="m1",
                is_accepted=True,
                parameters_snapshot={"temperature": 0.8},
            ),
            Continuation(
                id="c2",
                content="版本2",
                model="m2",
                is_accepted=False,
                reasoning_content="推理2",
            ),
        ],
    )
    json_str = ch.model_dump_json()
    restored = Chapter.model_validate_json(json_str)
    assert restored.id == "ch_rt"
    assert restored.project_id == "proj_rt"
    assert restored.index == 3
    assert restored.title == "第三章"
    assert restored.content == "章节正文"
    assert restored.word_count == 1500
    assert restored.metadata == {"notes": "章节备注", "tags": ["tag_a"]}
    assert len(restored.continuations) == 2
    assert isinstance(restored.continuations[0], Continuation)
    assert restored.continuations[0].id == "c1"
    assert restored.continuations[0].is_accepted is True
    assert restored.continuations[0].parameters_snapshot == {"temperature": 0.8}
    assert restored.continuations[1].id == "c2"
    assert restored.continuations[1].content == "版本2"
    assert restored.continuations[1].reasoning_content == "推理2"
    # datetime 字段 round-trip 后仍为 datetime
    assert isinstance(restored.created_at, datetime)
    assert isinstance(restored.updated_at, datetime)


def test_chapter_roundtrip_with_agent_artifacts() -> None:
    """Chapter 含 agent_artifacts 的 Continuation round-trip（dict 类型，向后兼容）。"""
    cont = Continuation(
        id="c_agent",
        content="正文",
        model="m",
        agent_artifacts={
            "revision_rounds": 2,
            "phase_logs": [{"phase": "writing", "ok": True}],
        },
    )
    ch = Chapter(
        id="ch_agent_rt",
        project_id="proj",
        index=0,
        continuations=[cont],
    )
    restored = Chapter.model_validate_json(ch.model_dump_json())
    assert len(restored.continuations) == 1
    assert restored.continuations[0].agent_artifacts is not None
    assert isinstance(restored.continuations[0].agent_artifacts, dict)
    assert restored.continuations[0].agent_artifacts["revision_rounds"] == 2
    assert restored.continuations[0].agent_artifacts["phase_logs"][0]["phase"] == "writing"


# ===== 6. datetime 字段（created_at / updated_at） =====


def test_chapter_datetime_defaults() -> None:
    """Chapter datetime 字段有默认值（datetime.now）。"""
    ch = Chapter(id="ch", project_id="p", index=0)
    assert isinstance(ch.created_at, datetime)
    assert isinstance(ch.updated_at, datetime)


def test_chapter_datetime_custom() -> None:
    """Chapter datetime 字段支持自定义时间。"""
    created = datetime(2026, 1, 1, 0, 0, 0)
    updated = datetime(2026, 6, 27, 15, 30, 0)
    ch = Chapter(
        id="ch",
        project_id="p",
        index=0,
        created_at=created,
        updated_at=updated,
    )
    assert ch.created_at == created
    assert ch.updated_at == updated


def test_chapter_datetime_roundtrip() -> None:
    """Chapter datetime 字段 round-trip 后仍为 datetime 类型。"""
    created = datetime(2026, 6, 27, 10, 0, 0)
    updated = datetime(2026, 6, 27, 12, 0, 0)
    ch = Chapter(
        id="ch",
        project_id="p",
        index=0,
        created_at=created,
        updated_at=updated,
    )
    restored = Chapter.model_validate_json(ch.model_dump_json())
    assert isinstance(restored.created_at, datetime)
    assert isinstance(restored.updated_at, datetime)
    assert restored.created_at == created
    assert restored.updated_at == updated


# ===== 7. source_chapter_range tuple 序列化（tuple → list in JSON → back） =====


def test_continuation_extracted_context_source_chapter_range_roundtrip() -> None:
    """Continuation.extracted_context_snapshot 中 ContextEntry 的
    source_chapter_range tuple 经 JSON round-trip 后还原为 tuple。"""
    cont = Continuation(
        id="c_range",
        content="正文",
        model="m",
        extracted_context_snapshot=[
            ContextEntry(
                uid="e1",
                category="characters",
                content="人物信息",
                source_chapter_range=(0, 4),
            ),
            ContextEntry(
                uid="e2",
                category="locations",
                content="地点信息",
                source_chapter_range=(2, 7),
            ),
        ],
    )
    # JSON 中 tuple 被序列化为 list
    json_str = cont.model_dump_json()
    import json
    data = json.loads(json_str)
    assert data["extracted_context_snapshot"][0]["source_chapter_range"] == [0, 4]
    assert data["extracted_context_snapshot"][1]["source_chapter_range"] == [2, 7]

    # 反序列化后应还原为 tuple
    restored = Continuation.model_validate_json(json_str)
    assert len(restored.extracted_context_snapshot) == 2
    assert restored.extracted_context_snapshot[0].source_chapter_range == (0, 4)
    assert restored.extracted_context_snapshot[1].source_chapter_range == (2, 7)
    # 确认类型为 tuple
    assert isinstance(restored.extracted_context_snapshot[0].source_chapter_range, tuple)


def test_source_chapter_range_none_roundtrip() -> None:
    """source_chapter_range 为 None 时 round-trip 保持 None。"""
    cont = Continuation(
        id="c_none",
        content="正文",
        model="m",
        extracted_context_snapshot=[
            ContextEntry(uid="e1", content="无区间"),
        ],
    )
    restored = Continuation.model_validate_json(cont.model_dump_json())
    assert restored.extracted_context_snapshot[0].source_chapter_range is None


def test_chapter_nested_continuation_with_source_chapter_range() -> None:
    """Chapter 嵌套含 source_chapter_range 的 Continuation round-trip。"""
    cont = Continuation(
        id="c_nested",
        content="正文",
        model="m",
        extracted_context_snapshot=[
            ContextEntry(
                uid="e1",
                content="条目",
                source_chapter_range=(1, 5),
            ),
        ],
    )
    ch = Chapter(
        id="ch_nested",
        project_id="proj",
        index=0,
        continuations=[cont],
    )
    restored = Chapter.model_validate_json(ch.model_dump_json())
    assert len(restored.continuations) == 1
    assert len(restored.continuations[0].extracted_context_snapshot) == 1
    assert restored.continuations[0].extracted_context_snapshot[0].source_chapter_range == (1, 5)
    assert isinstance(
        restored.continuations[0].extracted_context_snapshot[0].source_chapter_range, tuple
    )


# ===== 8. populate_by_name 与从 dict 构造 =====


def test_chapter_from_dict_partial() -> None:
    """Chapter 可从部分 dict 构造，缺失字段使用默认值。"""
    ch = Chapter.model_validate({
        "id": "ch_dict",
        "project_id": "p_dict",
        "index": 2,
    })
    assert ch.id == "ch_dict"
    assert ch.project_id == "p_dict"
    assert ch.index == 2
    assert ch.title == ""
    assert ch.continuations == []
    assert ch.metadata == {}


def test_continuation_from_dict_partial() -> None:
    """Continuation 可从部分 dict 构造，缺失字段使用默认值。"""
    cont = Continuation.model_validate({"id": "c_dict", "content": "内容"})
    assert cont.id == "c_dict"
    assert cont.content == "内容"
    assert cont.model == ""
    assert cont.parameters_snapshot == {}
    assert cont.prompt_snapshot == []


def test_chapter_with_empty_continuations_roundtrip() -> None:
    """Chapter 空 continuations 列表 round-trip 保持空。"""
    ch = Chapter(
        id="ch_empty",
        project_id="p",
        index=0,
        continuations=[],
    )
    restored = Chapter.model_validate_json(ch.model_dump_json())
    assert restored.continuations == []
