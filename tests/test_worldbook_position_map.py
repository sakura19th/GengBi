"""worldbook_importer 的 POSITION_MAP 修复正确性测试。

覆盖：
1. POSITION_MAP 包含 0-7 全部 8 个键
2. 0/2/5 映射到 before
3. 1/3/6/7 映射到 after
4. 4 映射到 at_depth
5-7. import_worldbook 对 position=4/2/3 的端到端映射验证
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

from novelforge.services.worldbook_importer import POSITION_MAP, import_worldbook


class TestPositionMap:
    """POSITION_MAP 修复正确性测试。"""

    def test_position_map_has_all_8_entries(self) -> None:
        """POSITION_MAP 包含 0-7 全部 8 个键。"""
        assert set(POSITION_MAP.keys()) == {0, 1, 2, 3, 4, 5, 6, 7}
        assert len(POSITION_MAP) == 8

    def test_position_before_values(self) -> None:
        """0/2/5 都映射到 before。"""
        assert POSITION_MAP[0] == "before"  # before
        assert POSITION_MAP[2] == "before"  # ANTop → 归入 before
        assert POSITION_MAP[5] == "before"  # EMTop → 归入 before

    def test_position_after_values(self) -> None:
        """1/3/6/7 都映射到 after。"""
        assert POSITION_MAP[1] == "after"  # after
        assert POSITION_MAP[3] == "after"  # ANBottom → 归入 after
        assert POSITION_MAP[6] == "after"  # EMBottom → 归入 after
        assert POSITION_MAP[7] == "after"  # outlet → 归入 after（兜底）

    def test_position_at_depth(self) -> None:
        """4 映射到 at_depth。"""
        assert POSITION_MAP[4] == "at_depth"

    def test_import_worldbook_position_4_mapped_to_at_depth(
        self, tmp_path: Path
    ) -> None:
        """position=4 (atDepth) 的条目应映射为 at_depth。"""
        worldbook = {
            "entries": {
                "0": {
                    "uid": "e1",
                    "key": ["关键词"],
                    "comment": "条目",
                    "content": "内容",
                    "position": 4,
                    "depth": 4,
                }
            }
        }
        path = tmp_path / "wb.json"
        path.write_text(json.dumps(worldbook), encoding="utf-8")

        entries = import_worldbook(path)
        assert len(entries) == 1
        assert entries[0].position == "at_depth"

    def test_import_worldbook_position_2_mapped_to_before(
        self, tmp_path: Path
    ) -> None:
        """position=2 (ANTop) 应映射为 before。"""
        worldbook = {
            "entries": {
                "0": {
                    "uid": "e1",
                    "content": "内容",
                    "position": 2,
                }
            }
        }
        path = tmp_path / "wb.json"
        path.write_text(json.dumps(worldbook), encoding="utf-8")

        entries = import_worldbook(path)
        assert len(entries) == 1
        assert entries[0].position == "before"

    def test_import_worldbook_position_3_mapped_to_after(
        self, tmp_path: Path
    ) -> None:
        """position=3 (ANBottom) 应映射为 after。"""
        worldbook = {
            "entries": {
                "0": {
                    "uid": "e1",
                    "content": "内容",
                    "position": 3,
                }
            }
        }
        path = tmp_path / "wb.json"
        path.write_text(json.dumps(worldbook), encoding="utf-8")

        entries = import_worldbook(path)
        assert len(entries) == 1
        assert entries[0].position == "after"
