"""ProtagonistProfile 主角形象提取测试。

覆盖：
1. _filter_protagonist_dimensions 维度过滤与非 dict 值替换
2. _parse_protagonist_response JSON 解析 + markdown fence 去除 + 字段过滤
3. _merge_protagonist_fields growth_arc 直接覆盖（不按长度启发式）
4. _merge_protagonist_fields 其他维度序列化长度启发式合并
5. ExtractResult protagonist 相关字段默认值
6. ExtractResult 携带 protagonist_profile 构造
7. PROTAGONIST_DIMENSIONS 常量含 8 大维度
8. ProtagonistProfile 模型 8 维度校验
9. _safe_serialize_dim 安全序列化（dict/str/不可序列化对象）
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置离屏平台，避免在 CI 环境中需要显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


# ===== 测试工具 =====


def _make_extractor() -> Any:
    """构建 ContextExtractor 实例（带 mock StorageService 与 ConfigManager）。

    用于测试实例方法 _merge_protagonist_fields。
    """
    from novelforge.services.context_extractor import ContextExtractor

    storage_service = MagicMock()
    storage_service.storage = MagicMock()
    storage_service.storage.get_cache = AsyncMock(return_value=None)
    storage_service.storage.set_cache = AsyncMock(return_value=None)

    config_manager = MagicMock()
    config_manager.get_context_extract_settings.return_value = {
        "extractor_model": "gpt-4o-mini",
        "cache_enabled": True,
        "cache_ttl_hours": 24,
        "extractor_prompt_override": None,
        "lookback_chapters": 5,
    }
    config_manager.get_default_endpoint.return_value = {
        "id": "ep1",
        "base_url": "https://api.test.com/v1",
        "default_model": "gpt-4o-mini",
    }
    config_manager.decrypt_api_key.return_value = "sk-test"

    return ContextExtractor(storage_service, config_manager)


# ===== 1. _filter_protagonist_dimensions 测试 =====


class TestFilterProtagonistDimensions:
    """_filter_protagonist_dimensions 维度过滤测试。"""

    def test_filter_protagonist_dimensions(self) -> None:
        """测试 8 维度被保留、非 dict 值替换为空 dict、额外字段被丢弃。"""
        from novelforge.services.context_extractor import (
            PROTAGONIST_DIMENSIONS,
            _filter_protagonist_dimensions,
        )

        data: dict[str, Any] = {
            "basic_anchors": {"name": "张三", "age": 25},
            "personality_system": {"big_five": {"O": 8, "C": 7}},
            "motivation_system": "这不是 dict 应被替换",  # 非 dict → 空 dict
            "emotion_defense": {"defense": "denial"},
            "behavior_fingerprint": None,  # None → 空 dict
            "relationship_coordinate": {"power": "equal"},
            "growth_arc": {"stage": "denial"},
            "ooc_redlines": {"forbidden": ["lie"]},
            # 额外字段应被丢弃
            "extra_field": {"should": "be dropped"},
            "another_extra": 123,
        }

        result = _filter_protagonist_dimensions(data)

        # 8 维度全部存在
        assert set(result.keys()) == set(PROTAGONIST_DIMENSIONS)
        assert len(result) == 8

        # dict 值原样保留
        assert result["basic_anchors"] == {"name": "张三", "age": 25}
        assert result["personality_system"] == {"big_five": {"O": 8, "C": 7}}
        assert result["emotion_defense"] == {"defense": "denial"}
        assert result["relationship_coordinate"] == {"power": "equal"}
        assert result["growth_arc"] == {"stage": "denial"}
        assert result["ooc_redlines"] == {"forbidden": ["lie"]}

        # 非 dict 值替换为空 dict
        assert result["motivation_system"] == {}
        assert result["behavior_fingerprint"] == {}

        # 额外字段被丢弃
        assert "extra_field" not in result
        assert "another_extra" not in result

    def test_filter_protagonist_dimensions_empty_input(self) -> None:
        """测试空字典输入返回 8 维度全空 dict。"""
        from novelforge.services.context_extractor import (
            PROTAGONIST_DIMENSIONS,
            _filter_protagonist_dimensions,
        )

        result = _filter_protagonist_dimensions({})
        assert set(result.keys()) == set(PROTAGONIST_DIMENSIONS)
        assert all(v == {} for v in result.values())


# ===== 2. _parse_protagonist_response 测试 =====


class TestParseProtagonistResponse:
    """_parse_protagonist_response JSON 解析测试。"""

    def test_parse_protagonist_response(self) -> None:
        """测试 JSON 解析 + markdown fence 去除 + 字段过滤。"""
        from novelforge.services.context_extractor import (
            PROTAGONIST_DIMENSIONS,
            _parse_protagonist_response,
        )

        # --- 场景 1：直接 JSON 对象 ---
        content_obj = json.dumps({
            "basic_anchors": {"name": "李四"},
            "personality_system": {"mbti": "INTJ"},
            "growth_arc": {"stage": "struggle"},
            "extra_field": "should be dropped",
        })
        result = _parse_protagonist_response(content_obj)
        assert set(result.keys()) == set(PROTAGONIST_DIMENSIONS)
        assert result["basic_anchors"] == {"name": "李四"}
        assert result["personality_system"] == {"mbti": "INTJ"}
        assert result["growth_arc"] == {"stage": "struggle"}
        assert "extra_field" not in result
        # 未提供的维度为空 dict
        assert result["motivation_system"] == {}
        assert result["ooc_redlines"] == {}

        # --- 场景 2：带 ```json markdown fence ---
        content_fenced = (
            "```json\n"
            + json.dumps({
                "basic_anchors": {"name": "王五"},
                "ooc_redlines": {"forbidden": ["kill"]},
            })
            + "\n```"
        )
        result_fenced = _parse_protagonist_response(content_fenced)
        assert result_fenced["basic_anchors"] == {"name": "王五"}
        assert result_fenced["ooc_redlines"] == {"forbidden": ["kill"]}
        assert set(result_fenced.keys()) == set(PROTAGONIST_DIMENSIONS)

        # --- 场景 3：带普通 ``` fence ---
        content_plain_fence = (
            "```\n"
            + json.dumps({"growth_arc": {"stage": "epiphany"}})
            + "\n```"
        )
        result_plain = _parse_protagonist_response(content_plain_fence)
        assert result_plain["growth_arc"] == {"stage": "epiphany"}
        assert set(result_plain.keys()) == set(PROTAGONIST_DIMENSIONS)

    def test_parse_protagonist_response_invalid_raises(self) -> None:
        """测试无效 JSON 抛出 JSONDecodeError。"""
        from novelforge.services.context_extractor import (
            _parse_protagonist_response,
        )

        with pytest.raises(json.JSONDecodeError):
            _parse_protagonist_response("not a json at all")


# ===== 3. _merge_protagonist_fields growth_arc 覆盖测试 =====


class TestMergeProtagonistFieldsGrowthArc:
    """_merge_protagonist_fields growth_arc 直接覆盖测试。"""

    def test_merge_protagonist_fields_growth_arc_override(self) -> None:
        """测试 growth_arc 新批次非空时直接覆盖旧值（不按长度启发式）。

        growth_arc 维度特殊：主角弧光可能演变（阶段推进/人格变化/依恋转变），
        新批次非空时直接覆盖旧值，确保反映至当前章节最新状态。
        即使旧值序列化长度更长，新批次仍直接覆盖。
        """
        extractor = _make_extractor()

        accumulated = {
            "growth_arc": {
                "stage": "denial",
                "detail": "这是一段非常长的详细描述用于验证长度启发式不会生效"
                "因为 growth_arc 应该直接覆盖而不是按长度合并"
                "所以即使旧值更长新批次非空时也会被直接覆盖",
                "unresolved_crisis": "identity vs role confusion",
            },
        }
        new_batch = {
            "growth_arc": {
                "stage": "epiphany",  # 短值，但应直接覆盖
            },
        }

        result = extractor._merge_protagonist_fields(accumulated, new_batch)

        # growth_arc 应被新批次直接覆盖（不是按字段合并）
        assert result["growth_arc"] == {"stage": "epiphany"}
        # 旧值的 detail/unresolved_crisis 不应保留（直接覆盖，非字段级合并）
        assert "detail" not in result["growth_arc"]
        assert "unresolved_crisis" not in result["growth_arc"]

    def test_merge_protagonist_fields_growth_arc_new_empty_keeps_old(self) -> None:
        """测试 growth_arc 新批次为空时保留旧值。"""
        extractor = _make_extractor()

        accumulated = {
            "growth_arc": {"stage": "denial", "detail": "old arc"},
        }
        new_batch = {
            "growth_arc": {},  # 新批次为空
        }

        result = extractor._merge_protagonist_fields(accumulated, new_batch)
        assert result["growth_arc"] == {"stage": "denial", "detail": "old arc"}


# ===== 4. _merge_protagonist_fields 长度启发式测试 =====


class TestMergeProtagonistFieldsLengthHeuristic:
    """_merge_protagonist_fields 其他维度序列化长度启发式测试。"""

    def test_merge_protagonist_fields_length_heuristic(self) -> None:
        """测试非 growth_arc 维度使用序列化长度启发式（新值更长或相等时覆盖）。"""
        extractor = _make_extractor()

        accumulated = {
            "basic_anchors": {
                "name": "张三",  # 旧值短
                "age": 25,
                "kept_field": "old value kept",  # 新批次无此字段，应保留
            },
        }
        new_batch = {
            "basic_anchors": {
                "name": "张三三三三三三三",  # 新值更长 → 覆盖
                "age": 30,  # 新值 30 vs 旧值 25，序列化 "30" 与 "25" 等长 → 新值覆盖
                "new_field": "added from new batch",  # 旧批次无此字段 → 新增
            },
        }

        result = extractor._merge_protagonist_fields(accumulated, new_batch)

        # name: 新值更长 → 覆盖
        assert result["basic_anchors"]["name"] == "张三三三三三三三"
        # age: 序列化长度相等（"30" 与 "25" 均 2 字符）→ 新值覆盖
        assert result["basic_anchors"]["age"] == 30
        # kept_field: 新批次无此字段 → 保留旧值
        assert result["basic_anchors"]["kept_field"] == "old value kept"
        # new_field: 旧批次无此字段 → 新增
        assert result["basic_anchors"]["new_field"] == "added from new batch"

    def test_merge_protagonist_fields_old_longer_kept(self) -> None:
        """测试旧值更长时保留旧值（长度启发式：新值更短不覆盖）。"""
        extractor = _make_extractor()

        accumulated = {
            "personality_system": {
                "core_narrative": "非常非常长的核心自我叙事描述应该被保留",  # 旧值长
            },
        }
        new_batch = {
            "personality_system": {
                "core_narrative": "短",  # 新值短 → 不覆盖
            },
        }

        result = extractor._merge_protagonist_fields(accumulated, new_batch)
        # 旧值更长 → 保留旧值
        assert result["personality_system"]["core_narrative"] == "非常非常长的核心自我叙事描述应该被保留"

    def test_merge_protagonist_fields_both_empty(self) -> None:
        """测试双侧均为空时返回空 dict。"""
        extractor = _make_extractor()

        accumulated = {"motivation_system": {}}
        new_batch = {"motivation_system": {}}

        result = extractor._merge_protagonist_fields(accumulated, new_batch)
        assert result["motivation_system"] == {}

    def test_merge_protagonist_fields_one_side_empty(self) -> None:
        """测试一侧为空时取另一侧。"""
        extractor = _make_extractor()

        # 旧空、新非空 → 取新
        accumulated = {"emotion_defense": {}}
        new_batch = {"emotion_defense": {"defense": "projection"}}
        result = extractor._merge_protagonist_fields(accumulated, new_batch)
        assert result["emotion_defense"] == {"defense": "projection"}

        # 旧非空、新空 → 取旧
        accumulated = {"emotion_defense": {"defense": "denial"}}
        new_batch = {"emotion_defense": {}}
        result = extractor._merge_protagonist_fields(accumulated, new_batch)
        assert result["emotion_defense"] == {"defense": "denial"}


# ===== 5. ExtractResult protagonist 字段默认值测试 =====


class TestExtractResultProtagonistFields:
    """ExtractResult protagonist 相关字段默认值测试。"""

    def test_extract_result_protagonist_fields(self) -> None:
        """测试 ExtractResult protagonist 相关字段默认值。"""
        from novelforge.services.context_extractor import ExtractResult

        result = ExtractResult()
        assert result.protagonist_profile is None
        assert result.protagonist_batch_count == 1
        assert result.protagonist_merged is False

        # 字段应存在于 dataclass fields
        assert "protagonist_profile" in ExtractResult.__dataclass_fields__
        assert "protagonist_batch_count" in ExtractResult.__dataclass_fields__
        assert "protagonist_merged" in ExtractResult.__dataclass_fields__


# ===== 6. ExtractResult 携带 protagonist_profile 构造测试 =====


class TestExtractResultProtagonistConstruction:
    """ExtractResult 携带 protagonist_profile 构造测试。"""

    def test_extract_result_protagonist_construction(self) -> None:
        """测试 ExtractResult 可携带 protagonist_profile 构造。"""
        from novelforge.models import ProtagonistProfile
        from novelforge.services.context_extractor import ExtractResult

        profile = ProtagonistProfile(
            basic_anchors={"name": "赵六", "age": 30},
            personality_system={"mbti": "ENFP"},
            motivation_system={"core_fear": "abandonment"},
            emotion_defense={"defense": "intellectualization"},
            behavior_fingerprint={"habit": "coffee"},
            relationship_coordinate={"power": "equal"},
            growth_arc={"stage": "practice"},
            ooc_redlines={"forbidden": ["betray"]},
            extracted_at=datetime(2024, 1, 1, 12, 0, 0),
            source_chapter_range=(0, 5),
        )

        result = ExtractResult(
            entries=[],
            status="completed",
            protagonist_profile=profile,
            protagonist_batch_count=3,
            protagonist_merged=True,
        )

        assert result.protagonist_profile is not None
        assert result.protagonist_profile.basic_anchors == {"name": "赵六", "age": 30}
        assert result.protagonist_profile.personality_system == {"mbti": "ENFP"}
        assert result.protagonist_profile.growth_arc == {"stage": "practice"}
        assert result.protagonist_profile.ooc_redlines == {"forbidden": ["betray"]}
        assert result.protagonist_profile.extracted_at == datetime(2024, 1, 1, 12, 0, 0)
        assert result.protagonist_profile.source_chapter_range == (0, 5)
        assert result.protagonist_batch_count == 3
        assert result.protagonist_merged is True


# ===== 7. PROTAGONIST_DIMENSIONS 常量测试 =====


class TestProtagonistDimensionsConstant:
    """PROTAGONIST_DIMENSIONS 常量测试。"""

    def test_protagonist_dimensions_constant(self) -> None:
        """测试 PROTAGONIST_DIMENSIONS 含 8 大预期维度。"""
        from novelforge.services.context_extractor import PROTAGONIST_DIMENSIONS

        expected = (
            "basic_anchors",
            "personality_system",
            "motivation_system",
            "emotion_defense",
            "behavior_fingerprint",
            "relationship_coordinate",
            "growth_arc",
            "ooc_redlines",
        )

        assert isinstance(PROTAGONIST_DIMENSIONS, tuple)
        assert len(PROTAGONIST_DIMENSIONS) == 8
        assert PROTAGONIST_DIMENSIONS == expected
        # 确保顺序一致
        for i, dim in enumerate(expected):
            assert PROTAGONIST_DIMENSIONS[i] == dim


# ===== 8. ProtagonistProfile 模型校验测试 =====


class TestProtagonistModelValidation:
    """ProtagonistProfile 模型 8 维度校验测试。"""

    def test_protect_protagonist_model_validation(self) -> None:
        """测试 ProtagonistProfile 模型 8 维度校验与默认值。"""
        from novelforge.models import ProtagonistProfile

        # --- 默认值：8 维度均为空 dict ---
        profile_default = ProtagonistProfile()
        assert profile_default.basic_anchors == {}
        assert profile_default.personality_system == {}
        assert profile_default.motivation_system == {}
        assert profile_default.emotion_defense == {}
        assert profile_default.behavior_fingerprint == {}
        assert profile_default.relationship_coordinate == {}
        assert profile_default.growth_arc == {}
        assert profile_default.ooc_redlines == {}
        assert profile_default.extracted_at is None
        assert profile_default.source_chapter_range is None

        # --- 构造含全部 8 维度 ---
        now = datetime.now()
        profile = ProtagonistProfile(
            basic_anchors={"name": "钱七", "age": 28, "gender": "male"},
            personality_system={"big_five": {"O": 9, "C": 6, "E": 7, "A": 5, "N": 4}},
            motivation_system={"core_desire": "freedom", "core_fear": "death"},
            emotion_defense={"defense_mechanism": "sublimation"},
            behavior_fingerprint={"body_language": "crosses arms when nervous"},
            relationship_coordinate={"boundary": "soft"},
            growth_arc={"stage": "trigger", "turning_point": "loss of mentor"},
            ooc_redlines={"must_do": ["protect the innocent"]},
            extracted_at=now,
            source_chapter_range=(2, 8),
        )
        assert profile.basic_anchors == {"name": "钱七", "age": 28, "gender": "male"}
        assert profile.personality_system == {
            "big_five": {"O": 9, "C": 6, "E": 7, "A": 5, "N": 4}
        }
        assert profile.motivation_system == {
            "core_desire": "freedom", "core_fear": "death"
        }
        assert profile.emotion_defense == {"defense_mechanism": "sublimation"}
        assert profile.behavior_fingerprint == {
            "body_language": "crosses arms when nervous"
        }
        assert profile.relationship_coordinate == {"boundary": "soft"}
        assert profile.growth_arc == {
            "stage": "trigger", "turning_point": "loss of mentor"
        }
        assert profile.ooc_redlines == {"must_do": ["protect the innocent"]}
        assert profile.extracted_at == now
        assert profile.source_chapter_range == (2, 8)

    def test_protagonist_profile_roundtrip(self) -> None:
        """测试 ProtagonistProfile 序列化/反序列化往返不丢字段。"""
        from novelforge.models import ProtagonistProfile

        now = datetime(2024, 6, 15, 10, 30, 0)
        original = ProtagonistProfile(
            basic_anchors={"name": "孙八"},
            growth_arc={"stage": "denial"},
            ooc_redlines={"forbidden": ["lie"]},
            extracted_at=now,
            source_chapter_range=(0, 3),
        )

        dumped = original.model_dump(mode="json")
        restored = ProtagonistProfile.model_validate(dumped)

        assert restored.basic_anchors == {"name": "孙八"}
        assert restored.growth_arc == {"stage": "denial"}
        assert restored.ooc_redlines == {"forbidden": ["lie"]}
        # 未设置的维度保持空 dict
        assert restored.personality_system == {}
        assert restored.motivation_system == {}
        assert restored.extracted_at is not None
        assert restored.source_chapter_range == (0, 3)


# ===== 9. _safe_serialize_dim 安全序列化测试 =====


class TestSafeSerializeDim:
    """_safe_serialize_dim 安全序列化测试。"""

    def test_safe_serialize_dim(self) -> None:
        """测试 dict/str/不可序列化对象的安全序列化。"""
        from novelforge.services.context_extractor import _safe_serialize_dim

        # --- dict：返回 JSON 字符串 ---
        dict_val = {"name": "张三", "age": 25}
        result_dict = _safe_serialize_dim(dict_val)
        assert isinstance(result_dict, str)
        assert json.loads(result_dict) == dict_val
        # 含中文时 ensure_ascii=False
        assert "张三" in result_dict

        # --- str：直接返回原字符串（不经过 json.dumps） ---
        str_val = "hello world"
        result_str = _safe_serialize_dim(str_val)
        assert result_str == "hello world"
        assert isinstance(result_str, str)

        # --- 空字符串：直接返回 ---
        assert _safe_serialize_dim("") == ""

        # --- 嵌套 dict：返回 JSON 字符串 ---
        nested = {"a": {"b": [1, 2, 3]}}
        result_nested = _safe_serialize_dim(nested)
        assert isinstance(result_nested, str)
        assert json.loads(result_nested) == nested

        # --- 不可序列化对象（set）：json.dumps 抛 TypeError → 回退 str(value) ---
        set_val = {1, 2, 3}
        result_set = _safe_serialize_dim(set_val)
        assert isinstance(result_set, str)
        # set 的 str 表示形如 "{1, 2, 3}"
        assert "1" in result_set and "2" in result_set and "3" in result_set

        # --- 自定义不可序列化对象：回退 str(value) ---
        class CustomObject:
            def __str__(self) -> str:
                return "custom_repr"

        custom = CustomObject()
        result_custom = _safe_serialize_dim(custom)
        assert result_custom == "custom_repr"

        # --- int：可序列化为 JSON ---
        assert _safe_serialize_dim(42) == "42"

        # --- list：可序列化为 JSON ---
        assert _safe_serialize_dim([1, 2, 3]) == "[1, 2, 3]"

        # --- None：可序列化为 JSON ---
        assert _safe_serialize_dim(None) == "null"


# ===== 10. _extract_protagonist 批次重试测试 =====


class TestExtractProtagonistBatchRetry:
    """_extract_protagonist 批次失败自动重试测试。"""

    def test_protagonist_batch_retry_on_llm_error(self) -> None:
        """单批次首次 LLM 调用失败，第二次成功 → 提取成功，call_count==2。"""
        import asyncio

        from novelforge.models import Chapter, NovelProfile, Project
        from novelforge.services.llm_client import LLMError

        extractor = _make_extractor()

        # 构造含 8 维度之一的有效主角形象 JSON
        profile_json = json.dumps(
            {"basic_anchors": {"name": "主角", "identity": "战士"}},
            ensure_ascii=False,
        )
        mock_client = MagicMock()
        mock_client.chat_completion = AsyncMock(
            side_effect=[
                LLMError("首次调用网络错误"),
                {
                    "choices": [{"message": {"content": profile_json}}],
                    "usage": {},
                },
            ]
        )

        profile = NovelProfile(
            title="测试小说",
            author="测试作者",
            protagonist="主角",
            synopsis="测试简介",
            world_setting="测试世界观",
            writing_style="测试风格",
        )
        project = Project(id="test_proj", name="测试小说", novel_profile=profile)
        chapters = [
            Chapter(
                id="ch_0",
                project_id="test_proj",
                index=0,
                title="第1章",
                content="章节内容",
                word_count=4,
            )
        ]
        config = {"lookback_chapters": 5}

        result, batch_count, merged = asyncio.run(
            extractor._extract_protagonist(
                project=project,
                batches=[chapters],
                config=config,
                client=mock_client,
                model="gpt-4o-mini",
                stream=False,
                on_chunk=None,
                on_batch_complete=None,
            )
        )

        assert result is not None
        assert batch_count == 1
        # 首次失败 + 第二次成功 = 2 次 LLM 调用
        assert mock_client.chat_completion.call_count == 2


if __name__ == "__main__":
    # 直接运行时执行所有测试
    pytest.main([__file__, "-v", "--tb=short"])
