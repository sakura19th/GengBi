"""功能 1「复制上下文到章节」测试脚本。

验证内容：
1. ``ContextEntry.copied_from`` 字段默认值与序列化往返
2. ``model_copy(update={...})`` 生成新 uid 与 copied_from 标记
3. ``generate_id("ctx_")`` 唯一性与前缀
4. 复制条目保留原 ``source_chapter_range``
5. UI 层 source 描述拼接逻辑（复制自第N章+原范围/单复制/导入/原范围）

运行方式：
    python -m pytest tests/test_context_copy_to_chapter.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from novelforge.models import ContextEntry
from novelforge.utils.ids import generate_id


# ===== ContextEntry.copied_from 字段 =====


class TestCopiedFromField:
    """``ContextEntry.copied_from`` 字段行为。"""

    __test__ = True

    def test_default_is_none(self) -> None:
        """新条目默认 copied_from=None。"""
        entry = ContextEntry(uid="ctx_1", content="测试")
        assert entry.copied_from is None

    def test_set_copied_from(self) -> None:
        """可显式设置 copied_from。"""
        entry = ContextEntry(uid="ctx_1", content="测试", copied_from=100)
        assert entry.copied_from == 100

    def test_serialization_roundtrip(self) -> None:
        """copied_from 字段经 model_dump/model_validate 往返保持一致。"""
        entry = ContextEntry(
            uid="ctx_1",
            content="测试",
            copied_from=99,
            source_chapter_range=(95, 100),
        )
        data = entry.model_dump(mode="json")
        assert data["copied_from"] == 99
        restored = ContextEntry.model_validate(data)
        assert restored.copied_from == 99

    def test_serialization_none_omittable(self) -> None:
        """copied_from=None 时序列化不含非 None 值。"""
        entry = ContextEntry(uid="ctx_1", content="测试")
        data = entry.model_dump(mode="json")
        assert data["copied_from"] is None


# ===== model_copy 生成新 uid + copied_from =====


class TestModelCopyForCopyFeature:
    """模拟 MainWindow._on_copy_context_to_chapter 中的 model_copy 行为。"""

    __test__ = True

    def test_model_copy_updates_uid_and_copied_from(self) -> None:
        """model_copy(update={uid, copied_from}) 生成新条目，原条目不变。"""
        original = ContextEntry(
            uid="ctx_original",
            category="characters",
            key=["主角"],
            content="主角张三",
            order=100,
            position="before",
            source_chapter_range=(95, 100),
        )
        new_uid = generate_id("ctx_")
        copied = original.model_copy(update={
            "uid": new_uid,
            "copied_from": 100,
        })
        # 新条目字段更新
        assert copied.uid == new_uid
        assert copied.copied_from == 100
        # 其他字段保留（含 source_chapter_range）
        assert copied.content == "主角张三"
        assert copied.source_chapter_range == (95, 100)
        assert copied.category == "characters"
        # 原条目不被修改
        assert original.uid == "ctx_original"
        assert original.copied_from is None

    def test_model_copy_preserves_disabled_state(self) -> None:
        """复制时保留 enabled 状态。"""
        original = ContextEntry(
            uid="ctx_original",
            content="测试",
            enabled=False,
        )
        copied = original.model_copy(update={
            "uid": generate_id("ctx_"),
            "copied_from": 50,
        })
        assert copied.enabled is False

    def test_model_copy_multiple_unique_uids(self) -> None:
        """批量复制时每条 uid 唯一。"""
        original = ContextEntry(uid="ctx_orig", content="测试")
        uids = set()
        for _ in range(20):
            new_uid = generate_id("ctx_")
            uids.add(new_uid)
            copied = original.model_copy(update={
                "uid": new_uid,
                "copied_from": 100,
            })
            assert copied.uid == new_uid
        # 20 次生成的 uid 应唯一
        assert len(uids) == 20


# ===== generate_id 行为 =====


class TestGenerateId:
    """``generate_id("ctx_")`` 唯一性与前缀。"""

    __test__ = True

    def test_prefix(self) -> None:
        """生成的 ID 含 ctx_ 前缀。"""
        uid = generate_id("ctx_")
        assert uid.startswith("ctx_")

    def test_uniqueness(self) -> None:
        """连续生成 100 次 ID 应唯一。"""
        uids = {generate_id("ctx_") for _ in range(100)}
        assert len(uids) == 100

    def test_non_empty_after_prefix(self) -> None:
        """前缀后应有非空 hex 部分。"""
        uid = generate_id("ctx_")
        assert len(uid) > len("ctx_")


# ===== Source 描述拼接逻辑 =====


class TestSourceDescriptionLogic:
    """镜像 ContextPreviewPanel 中 source 描述拼接逻辑（不依赖 Qt）。"""

    __test__ = True

    @staticmethod
    def _build_source_meta(entry: ContextEntry) -> str:
        """复刻 context_preview_panel._build_entry_widget 中的 source 拼接逻辑。"""
        if entry.copied_from is not None:
            if entry.source_chapter_range is not None:
                return (
                    f"source=复制自第{entry.copied_from}章"
                    f"（原第{entry.source_chapter_range[0]}-{entry.source_chapter_range[1]}章）"
                )
            return f"source=复制自第{entry.copied_from}章"
        if entry.source_chapter_range is not None:
            return (
                f"source=第{entry.source_chapter_range[0]}-{entry.source_chapter_range[1]}章"
            )
        return "source=导入"

    def test_copied_with_range(self) -> None:
        """复制自第100章+原范围95-100。"""
        entry = ContextEntry(
            uid="ctx_1",
            content="测试",
            copied_from=100,
            source_chapter_range=(95, 100),
        )
        meta = self._build_source_meta(entry)
        assert meta == "source=复制自第100章（原第95-100章）"

    def test_copied_without_range(self) -> None:
        """仅复制自第100章（无原范围）。"""
        entry = ContextEntry(
            uid="ctx_1",
            content="测试",
            copied_from=100,
        )
        meta = self._build_source_meta(entry)
        assert meta == "source=复制自第100章"

    def test_extracted_with_range(self) -> None:
        """非复制、有原范围：source=第95-100章。"""
        entry = ContextEntry(
            uid="ctx_1",
            content="测试",
            source_chapter_range=(95, 100),
        )
        meta = self._build_source_meta(entry)
        assert meta == "source=第95-100章"

    def test_imported_no_meta(self) -> None:
        """无 copied_from 无 range：source=导入。"""
        entry = ContextEntry(uid="ctx_1", content="测试")
        meta = self._build_source_meta(entry)
        assert meta == "source=导入"

    def test_copied_takes_priority_over_range(self) -> None:
        """copied_from 优先于 source_chapter_range 决定描述前缀。"""
        entry = ContextEntry(
            uid="ctx_1",
            content="测试",
            copied_from=105,
            source_chapter_range=(95, 100),
        )
        meta = self._build_source_meta(entry)
        # 应以「复制自第105章」开头，不是「第95-100章」
        assert meta.startswith("source=复制自第105章")


# ===== 复制合并语义 =====


class TestCopyMergeSemantics:
    """模拟 MainWindow._on_copy_context_to_chapter 的追加合并语义。"""

    __test__ = True

    def test_merge_existing_with_copied(self) -> None:
        """目标章已有条目时，复制条目追加在后（合并保留 existing）。"""
        existing = [
            ContextEntry(uid="ctx_exist_1", content="已有1"),
            ContextEntry(uid="ctx_exist_2", content="已有2"),
        ]
        source_entries = [
            ContextEntry(
                uid="ctx_src_1",
                content="源1",
                source_chapter_range=(95, 100),
            ),
        ]
        copied_entries = [
            e.model_copy(update={
                "uid": generate_id("ctx_"),
                "copied_from": 100,
            })
            for e in source_entries
        ]
        merged = existing + copied_entries
        # 合并后条目数 = 现有 + 复制
        assert len(merged) == 3
        # 现有条目保留原 uid
        assert merged[0].uid == "ctx_exist_1"
        assert merged[1].uid == "ctx_exist_2"
        # 复制条目带新 uid 与 copied_from
        assert merged[2].uid != "ctx_src_1"
        assert merged[2].copied_from == 100
        # 复制条目保留 source_chapter_range
        assert merged[2].source_chapter_range == (95, 100)

    def test_merge_empty_existing(self) -> None:
        """目标章无条目时，复制条目独立构成 merged 列表。"""
        existing: list[ContextEntry] = []
        source_entries = [
            ContextEntry(uid="ctx_src_1", content="源1"),
            ContextEntry(uid="ctx_src_2", content="源2"),
        ]
        copied_entries = [
            e.model_copy(update={
                "uid": generate_id("ctx_"),
                "copied_from": 100,
            })
            for e in source_entries
        ]
        merged = existing + copied_entries
        assert len(merged) == 2
        assert all(e.copied_from == 100 for e in merged)
        # uid 全部新生成
        assert all(e.uid != "ctx_src_1" and e.uid != "ctx_src_2" for e in merged)
