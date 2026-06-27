"""Project 相关数据模型测试。

覆盖：
1. ManualOverride / ChapterSplitRule / NovelProfile / Project 默认值
2. ChapterSplitRule.pattern 默认正则
3. ManualOverride 字段构造与行为（纯数据模型，无 merge 方法）
4. 序列化 round-trip（model_dump_json → model_validate_json）
5. model_config populate_by_name 生效
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

from novelforge.models.project import (
    ChapterSplitRule,
    ManualOverride,
    NovelProfile,
    Project,
)


# ===== 1. ManualOverride 默认值 =====


def test_manual_override_defaults() -> None:
    """ManualOverride 默认值实例化。"""
    mo = ManualOverride()
    assert mo.action == "split"
    assert mo.chapter_id == ""
    assert mo.position == 0


def test_manual_override_custom_values() -> None:
    """ManualOverride 支持自定义字段。"""
    mo = ManualOverride(action="merge", chapter_id="ch_1", position=120)
    assert mo.action == "merge"
    assert mo.chapter_id == "ch_1"
    assert mo.position == 120


def test_manual_override_is_plain_data_model() -> None:
    """ManualOverride 是纯数据模型，无 merge 方法。"""
    mo = ManualOverride()
    # 确认没有 merge 方法（仅记录拆分/合并操作）
    assert not hasattr(mo, "merge")


# ===== 2. ChapterSplitRule 默认值与正则 =====


def test_chapter_split_rule_defaults() -> None:
    """ChapterSplitRule 默认值实例化。"""
    rule = ChapterSplitRule()
    # 默认正则匹配中文章节标题
    assert rule.pattern == r"^第[一二三四五六七八九十百千零\d]+[章节回卷]"
    assert rule.include_title_in_content is False
    assert rule.manual_overrides == []


def test_chapter_split_rule_pattern_matches_chinese_title() -> None:
    """ChapterSplitRule 默认 pattern 能匹配常见中文章节标题。"""
    import re

    rule = ChapterSplitRule()
    pattern = re.compile(rule.pattern)
    # 匹配「第X章」「第X节」「第X回」「第X卷」
    assert pattern.match("第一章")
    assert pattern.match("第23章")
    assert pattern.match("第十节")
    assert pattern.match("第一百回")
    assert pattern.match("第5卷")
    # 不匹配的内容
    assert not pattern.match("开头")


def test_chapter_split_rule_with_manual_overrides() -> None:
    """ChapterSplitRule 嵌套 manual_overrides 列表。"""
    rule = ChapterSplitRule(
        pattern=r"^Chapter\s+\d+",
        include_title_in_content=True,
        manual_overrides=[
            ManualOverride(action="split", chapter_id="ch_1", position=500),
            ManualOverride(action="merge", chapter_id="ch_2"),
        ],
    )
    assert rule.pattern == r"^Chapter\s+\d+"
    assert rule.include_title_in_content is True
    assert len(rule.manual_overrides) == 2
    assert isinstance(rule.manual_overrides[0], ManualOverride)
    assert rule.manual_overrides[0].action == "split"
    assert rule.manual_overrides[0].position == 500
    assert rule.manual_overrides[1].action == "merge"


def test_chapter_split_rule_default_overrides_independent() -> None:
    """多个 ChapterSplitRule 实例的 manual_overrides 互不影响（default_factory 隔离）。"""
    r1 = ChapterSplitRule()
    r2 = ChapterSplitRule()
    r1.manual_overrides.append(ManualOverride(action="split"))
    # r2 不受影响
    assert r2.manual_overrides == []


# ===== 3. NovelProfile 默认值 =====


def test_novel_profile_defaults() -> None:
    """NovelProfile 默认值实例化（全部空字符串）。"""
    profile = NovelProfile()
    assert profile.title == ""
    assert profile.author == ""
    assert profile.protagonist == ""
    assert profile.synopsis == ""
    assert profile.world_setting == ""
    assert profile.writing_style == ""


def test_novel_profile_custom_values() -> None:
    """NovelProfile 支持自定义字段。"""
    profile = NovelProfile(
        title="我的小说",
        author="张三",
        protagonist="李四",
        synopsis="一个故事",
        world_setting="架空世界",
        writing_style="冷峻短句",
    )
    assert profile.title == "我的小说"
    assert profile.author == "张三"
    assert profile.protagonist == "李四"
    assert profile.synopsis == "一个故事"
    assert profile.world_setting == "架空世界"
    assert profile.writing_style == "冷峻短句"


# ===== 4. Project 默认值与必填字段 =====


def test_project_requires_id() -> None:
    """Project 必须提供 id 字段。"""
    with pytest.raises(ValidationError):
        Project()  # type: ignore[call-arg]


def test_project_defaults() -> None:
    """Project 提供 id 后其余字段使用默认值。"""
    p = Project(id="proj_1")
    assert p.id == "proj_1"
    assert p.name == ""
    assert p.source_file == ""
    assert p.preset_id == "default"
    assert p.regex_script_ids == []
    assert p.extract_config is None
    # 嵌套默认值
    assert isinstance(p.novel_profile, NovelProfile)
    assert p.novel_profile.title == ""
    assert isinstance(p.chapter_split_rule, ChapterSplitRule)
    assert p.chapter_split_rule.pattern == r"^第[一二三四五六七八九十百千零\d]+[章节回卷]"
    # datetime 字段应有默认值
    assert isinstance(p.created_at, datetime)
    assert isinstance(p.updated_at, datetime)


def test_project_full_construction() -> None:
    """Project 完整字段构造。"""
    p = Project(
        id="proj_2",
        name="测试项目",
        source_file="novel.txt",
        novel_profile=NovelProfile(title="书名", author="作者"),
        preset_id="preset_1",
        regex_script_ids=["rx_1", "rx_2"],
        extract_config={"extractor_model": "gpt-4", "cache_enabled": True},
        chapter_split_rule=ChapterSplitRule(pattern=r"^Chapter\s+\d+"),
    )
    assert p.name == "测试项目"
    assert p.source_file == "novel.txt"
    assert p.novel_profile.title == "书名"
    assert p.novel_profile.author == "作者"
    assert p.preset_id == "preset_1"
    assert p.regex_script_ids == ["rx_1", "rx_2"]
    assert p.extract_config == {"extractor_model": "gpt-4", "cache_enabled": True}
    assert p.chapter_split_rule.pattern == r"^Chapter\s+\d+"


def test_project_default_list_independent() -> None:
    """多个 Project 实例的 list 字段互不影响（default_factory 隔离）。"""
    p1 = Project(id="p1")
    p2 = Project(id="p2")
    p1.regex_script_ids.append("rx_a")
    # p2 不受影响
    assert p2.regex_script_ids == []


# ===== 5. 序列化 round-trip =====


def test_manual_override_roundtrip() -> None:
    """ManualOverride 序列化/反序列化 round-trip。"""
    mo = ManualOverride(action="merge", chapter_id="ch_9", position=42)
    json_str = mo.model_dump_json()
    restored = ManualOverride.model_validate_json(json_str)
    assert restored.action == "merge"
    assert restored.chapter_id == "ch_9"
    assert restored.position == 42


def test_chapter_split_rule_roundtrip() -> None:
    """ChapterSplitRule（含嵌套 manual_overrides）序列化 round-trip。"""
    rule = ChapterSplitRule(
        pattern=r"^Chapter\s+\d+",
        include_title_in_content=True,
        manual_overrides=[
            ManualOverride(action="split", chapter_id="ch_1", position=100),
            ManualOverride(action="merge", chapter_id="ch_2"),
        ],
    )
    json_str = rule.model_dump_json()
    restored = ChapterSplitRule.model_validate_json(json_str)
    assert restored.pattern == r"^Chapter\s+\d+"
    assert restored.include_title_in_content is True
    assert len(restored.manual_overrides) == 2
    assert restored.manual_overrides[0].action == "split"
    assert restored.manual_overrides[0].position == 100
    assert restored.manual_overrides[1].action == "merge"


def test_novel_profile_roundtrip() -> None:
    """NovelProfile 序列化/反序列化 round-trip。"""
    profile = NovelProfile(
        title="书名", author="作者", protagonist="主角",
        synopsis="简介", world_setting="世界", writing_style="风格",
    )
    json_str = profile.model_dump_json()
    restored = NovelProfile.model_validate_json(json_str)
    assert restored.title == "书名"
    assert restored.author == "作者"
    assert restored.protagonist == "主角"
    assert restored.synopsis == "简介"
    assert restored.world_setting == "世界"
    assert restored.writing_style == "风格"


def test_project_roundtrip() -> None:
    """Project（含嵌套对象）序列化/反序列化 round-trip。"""
    p = Project(
        id="proj_rt",
        name="round-trip 项目",
        source_file="source.txt",
        novel_profile=NovelProfile(title="书名", author="作者"),
        preset_id="preset_rt",
        regex_script_ids=["rx_1", "rx_2"],
        extract_config={"extractor_model": "gpt-4", "cache_ttl_hours": 24},
        chapter_split_rule=ChapterSplitRule(
            pattern=r"^Chapter\s+\d+",
            manual_overrides=[
                ManualOverride(action="split", chapter_id="ch_1", position=50),
            ],
        ),
    )
    json_str = p.model_dump_json()
    restored = Project.model_validate_json(json_str)
    assert restored.id == "proj_rt"
    assert restored.name == "round-trip 项目"
    assert restored.source_file == "source.txt"
    assert restored.novel_profile.title == "书名"
    assert restored.novel_profile.author == "作者"
    assert restored.preset_id == "preset_rt"
    assert restored.regex_script_ids == ["rx_1", "rx_2"]
    assert restored.extract_config == {"extractor_model": "gpt-4", "cache_ttl_hours": 24}
    assert restored.chapter_split_rule.pattern == r"^Chapter\s+\d+"
    assert len(restored.chapter_split_rule.manual_overrides) == 1
    assert restored.chapter_split_rule.manual_overrides[0].action == "split"
    assert restored.chapter_split_rule.manual_overrides[0].position == 50
    # datetime 字段 round-trip 后仍为 datetime
    assert isinstance(restored.created_at, datetime)
    assert isinstance(restored.updated_at, datetime)


# ===== 6. model_config populate_by_name 生效 =====


def test_project_populate_by_name() -> None:
    """Project populate_by_name=True：可用字段名构造。"""
    p = Project(id="p_pbn", name="名称")
    assert p.id == "p_pbn"
    assert p.name == "名称"


def test_project_from_dict_partial() -> None:
    """Project 可从部分 dict 构造，缺失字段使用默认值。"""
    p = Project.model_validate({"id": "p_dict", "name": "字典构造"})
    assert p.id == "p_dict"
    assert p.name == "字典构造"
    assert p.preset_id == "default"
    assert p.regex_script_ids == []
    assert p.extract_config is None


def test_extract_config_nullable() -> None:
    """Project.extract_config 可为 None 或 dict。"""
    p_none = Project(id="p1")
    assert p_none.extract_config is None

    p_dict = Project(id="p2", extract_config={"lookback_chapters": 5})
    assert p_dict.extract_config == {"lookback_chapters": 5}

    # round-trip 后 None 保持 None
    restored = Project.model_validate_json(p_none.model_dump_json())
    assert restored.extract_config is None
