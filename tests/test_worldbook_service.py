"""全局世界书服务（WorldBookService）CRUD 测试。

覆盖：
1. 创建空世界书（字段验证）
2. 保存与加载的 round-trip
3. list_worldbooks 按 name 排序
4. 删除世界书（.json 与 .bak 同时删除）
5. 启用/禁用切换与持久化
6. 从 ST JSON 导入
7. 导出为 ST JSON
8. 加载不存在的世界书返回 None
9. 主文件损坏时从 .bak 恢复
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

from novelforge.models.context import ContextEntry
from novelforge.models.worldbook import WorldBook
from novelforge.services.worldbook_service import WorldBookService


# 标准的 ST 世界书 JSON（dict 格式，含 2 条 entries），用于 import 测试
ST_WORLDBOOK_JSON: dict = {
    "entries": {
        "0": {
            "uid": 1,
            "key": ["主角"],
            "keysecondary": [None],
            "comment": "人物-主角",
            "content": "主角名叫张三",
            "constant": True,
            "vectorized": False,
            "selective": False,
            "selectiveLogic": 0,
            "addMemo": True,
            "order": 100,
            "position": 0,
            "disable": False,
            "excludeRecursion": False,
            "preventRecursion": False,
            "delayUntilRecursion": False,
            "probability": 100,
            "useProbability": True,
            "depth": 4,
            "group": "",
            "groupOverride": False,
            "groupWarned": False,
            "role": 0,
            "wrap": False,
            "displayIndex": 0,
        },
        "1": {
            "uid": 2,
            "key": ["地点-京城"],
            "comment": "地点-京城",
            "content": "京城是大陆第一城",
            "order": 100,
            "position": 1,
            "depth": 4,
            "role": 0,
        },
    }
}


class TestWorldBookServiceCRUD:
    """WorldBookService 的 CRUD 流程测试。"""

    def test_create_worldbook(self, tmp_path: Path) -> None:
        """创建空世界书，验证基础字段。"""
        service = WorldBookService(storage_path=tmp_path)
        wb = service.create_worldbook("我的世界书")

        assert wb.id.startswith("wb_")
        assert wb.name == "我的世界书"
        assert wb.entries == []
        assert wb.enabled is True
        # create_worldbook 未显式设置 created_at，保持模型默认 None
        assert wb.created_at is None
        # save_worldbook 会设置 updated_at
        assert wb.updated_at is not None
        # 文件应已写入磁盘
        wb_file = tmp_path / "worldbooks" / f"{wb.id}.json"
        assert wb_file.exists()

    def test_save_and_load_worldbook(self, tmp_path: Path) -> None:
        """创建后保存，重新加载，验证字段完整。"""
        service = WorldBookService(storage_path=tmp_path)
        wb = service.create_worldbook("可加载世界书")

        # 添加条目并修改字段后再次保存
        wb.entries = [
            ContextEntry(
                uid="char_1",
                category="characters",
                key=["主角"],
                comment="主角信息",
                content="主角是位战士",
                order=50,
                position="before",
            ),
            ContextEntry(
                uid="loc_1",
                category="locations",
                key=["京城"],
                comment="地点-京城",
                content="京城是大陆第一城",
                position="after",
            ),
        ]
        wb.enabled = False
        service.save_worldbook(wb)

        # 重新加载
        loaded = service.load_worldbook(wb.id)
        assert loaded is not None
        assert loaded.id == wb.id
        assert loaded.name == "可加载世界书"
        assert loaded.enabled is False
        assert loaded.updated_at is not None
        assert len(loaded.entries) == 2
        assert loaded.entries[0].uid == "char_1"
        assert loaded.entries[0].content == "主角是位战士"
        assert loaded.entries[0].position == "before"
        assert loaded.entries[0].order == 50
        assert loaded.entries[1].uid == "loc_1"
        assert loaded.entries[1].category == "locations"
        assert loaded.entries[1].position == "after"

    def test_list_worldbooks_sorted_by_name(self, tmp_path: Path) -> None:
        """创建多个世界书，验证 list_worldbooks 按 name 排序。"""
        service = WorldBookService(storage_path=tmp_path)
        service.create_worldbook("Zeta")
        service.create_worldbook("Alpha")
        service.create_worldbook("Middle")

        worldbooks = service.list_worldbooks()
        names = [w.name for w in worldbooks]
        assert names == ["Alpha", "Middle", "Zeta"]

    def test_delete_worldbook(self, tmp_path: Path) -> None:
        """创建后删除，验证 .json 与 .bak 均被删除，list 不再包含。"""
        service = WorldBookService(storage_path=tmp_path)
        wb = service.create_worldbook("待删除")
        wb_id = wb.id

        # 二次保存以产生 .bak
        wb.enabled = False
        service.save_worldbook(wb)

        wb_file = tmp_path / "worldbooks" / f"{wb_id}.json"
        bak_file = wb_file.with_suffix(wb_file.suffix + ".bak")
        assert wb_file.exists()
        assert bak_file.exists()

        assert service.delete_worldbook(wb_id) is True
        assert not wb_file.exists()
        assert not bak_file.exists()

        # list 不再包含
        worldbooks = service.list_worldbooks()
        assert all(w.id != wb_id for w in worldbooks)
        # load 也返回 None
        assert service.load_worldbook(wb_id) is None

    def test_set_worldbook_enabled(self, tmp_path: Path) -> None:
        """切换 enabled 状态并保存，重新加载验证。"""
        service = WorldBookService(storage_path=tmp_path)
        wb = service.create_worldbook("切换启用")
        assert wb.enabled is True

        # 禁用
        assert service.set_worldbook_enabled(wb.id, False) is True
        loaded = service.load_worldbook(wb.id)
        assert loaded is not None
        assert loaded.enabled is False

        # 重新启用
        assert service.set_worldbook_enabled(wb.id, True) is True
        loaded = service.load_worldbook(wb.id)
        assert loaded is not None
        assert loaded.enabled is True

    def test_import_from_st_json(self, tmp_path: Path) -> None:
        """准备临时 ST 世界书 JSON，调用 import_from_st_json，验证条目数、name、保存。"""
        service = WorldBookService(storage_path=tmp_path)

        # 准备 ST 世界书 JSON 文件（文件名带中文以验证 stem 提取）
        st_file = tmp_path / "我的设定集.json"
        st_file.write_text(
            json.dumps(ST_WORLDBOOK_JSON, ensure_ascii=False),
            encoding="utf-8",
        )

        wb = service.import_from_st_json(st_file)
        # name 取文件名 stem
        assert wb.name == "我的设定集"
        # 两条条目
        assert len(wb.entries) == 2
        # 条目 uid 正确（ST 的 uid=1/2 被转为字符串）
        uids = {e.uid for e in wb.entries}
        assert "1" in uids
        assert "2" in uids
        # 第一条：人物-主角，position=0 → before
        char_entry = next(e for e in wb.entries if e.uid == "1")
        assert char_entry.content == "主角名叫张三"
        assert char_entry.category == "characters"
        assert char_entry.position == "before"
        # 第二条：地点-京城，position=1 → after
        loc_entry = next(e for e in wb.entries if e.uid == "2")
        assert loc_entry.content == "京城是大陆第一城"
        assert loc_entry.category == "locations"
        assert loc_entry.position == "after"
        # 应已保存到文件系统
        wb_file = tmp_path / "worldbooks" / f"{wb.id}.json"
        assert wb_file.exists()
        # 重新加载仍可恢复
        loaded = service.load_worldbook(wb.id)
        assert loaded is not None
        assert len(loaded.entries) == 2

    def test_export_to_st_json(self, tmp_path: Path) -> None:
        """创建含 entries 的世界书，导出到临时文件，验证 JSON 结构。"""
        service = WorldBookService(storage_path=tmp_path)
        wb = service.create_worldbook("导出测试")
        wb.entries = [
            ContextEntry(
                uid="e1",
                key=["关键词1"],
                comment="条目1",
                content="内容1",
                position="before",
            ),
            ContextEntry(
                uid="e2",
                key=["关键词2"],
                comment="条目2",
                content="内容2",
                position="at_depth",
                depth=2,
            ),
        ]
        service.save_worldbook(wb)

        # 导出到临时文件
        export_file = tmp_path / "exports" / "exported.json"
        service.export_to_st_json(wb, export_file)
        assert export_file.exists()

        # 验证 JSON 结构
        data = json.loads(export_file.read_text(encoding="utf-8"))
        assert "entries" in data
        assert isinstance(data["entries"], dict)
        assert len(data["entries"]) == 2
        # 验证条目字段（赓笔 position 经反向映射回数字）
        entry0 = data["entries"]["0"]
        assert entry0["content"] == "内容1"
        assert entry0["position"] == 0  # before → 0
        assert entry0["uid"] == "e1"
        entry1 = data["entries"]["1"]
        assert entry1["content"] == "内容2"
        assert entry1["position"] == 4  # at_depth → 4
        assert entry1["depth"] == 2

    def test_load_nonexistent_returns_none(self, tmp_path: Path) -> None:
        """load_worldbook 不存在的 id，返回 None。"""
        service = WorldBookService(storage_path=tmp_path)
        assert service.load_worldbook("nonexistent_id") is None

    def test_load_corrupt_recovers_from_bak(self, tmp_path: Path) -> None:
        """主 JSON 损坏时，load_worldbook 应从 .bak 恢复。"""
        service = WorldBookService(storage_path=tmp_path)
        wb = service.create_worldbook("可恢复世界书")
        wb_id = wb.id

        # 二次保存以生成 .bak（.bak 会保存第一次的版本：enabled=True，entries=[]）
        wb.enabled = False
        wb.entries = [
            ContextEntry(uid="new_entry", content="第二次保存的条目")
        ]
        service.save_worldbook(wb)

        wb_file = tmp_path / "worldbooks" / f"{wb_id}.json"
        bak_file = wb_file.with_suffix(wb_file.suffix + ".bak")
        assert wb_file.exists()
        assert bak_file.exists()

        # 损坏主文件
        wb_file.write_text("这不是合法 JSON {{{", encoding="utf-8")

        # load_worldbook 应从 .bak 恢复，不抛出异常
        loaded = service.load_worldbook(wb_id)
        # load_json_with_recovery 支持 .bak 回退，返回 .bak 内容（第一次保存的版本）
        assert loaded is not None
        assert loaded.name == "可恢复世界书"
        assert loaded.enabled is True  # .bak 为第一次保存的版本
        assert loaded.entries == []
