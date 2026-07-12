"""流程控制插件系统测试。

覆盖：
- FlowPlugin/FlowStage 数据模型校验
- FlowPluginService CRUD + 导入导出 + 内置插件首启复制
- FlowExecutor 阶段执行 + 挂起/恢复 + cancel
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from novelforge.models import FlowPlugin, FlowStage
from novelforge.services.flow_executor import FlowExecutor
from novelforge.services.flow_plugin_service import FlowPluginService


# ===== 模型校验测试 =====


class TestFlowStageModel:
    """FlowStage 数据模型测试。"""

    def test_minimal_stage(self) -> None:
        """仅必填字段构建阶段。"""
        stage = FlowStage(id="write", agent="continuation")
        assert stage.id == "write"
        assert stage.agent == "continuation"
        assert stage.name == ""
        assert stage.flow_key == ""
        assert stage.streaming is True
        assert stage.created_by == ""
        assert stage.params == {}
        assert stage.input_from == ""

    def test_full_stage(self) -> None:
        """全字段构建阶段。"""
        stage = FlowStage(
            id="generate",
            name="重写生成",
            agent="continuation",
            flow_key="single_continuation",
            streaming=True,
            created_by="rewrite_current",
            params={"exclude_current": True},
            input_from="analysis",
        )
        assert stage.name == "重写生成"
        assert stage.params == {"exclude_current": True}
        assert stage.input_from == "analysis"

    def test_volume_phase_agent_type(self) -> None:
        """volume_phase agent 类型构建成功。"""
        stage = FlowStage(
            id="deep_analysis",
            agent="volume_phase",
            params={"phase": "deep_analysis"},
        )
        assert stage.agent == "volume_phase"
        assert stage.params == {"phase": "deep_analysis"}

    def test_invalid_agent_type(self) -> None:
        """非法 agent 类型校验。"""
        with pytest.raises(ValidationError):
            FlowStage(id="bad", agent="invalid_agent")


class TestFlowPluginModel:
    """FlowPlugin 数据模型测试。"""

    def test_minimal_plugin(self) -> None:
        """仅必填字段构建插件。"""
        plugin = FlowPlugin(id="test", name="测试插件")
        assert plugin.id == "test"
        assert plugin.name == "测试插件"
        assert plugin.builtin is False
        assert plugin.ui_mode == "standard"
        assert plugin.accept_mode == "promote"
        assert plugin.stages == []

    def test_plugin_with_stages(self) -> None:
        """带阶段的插件。"""
        plugin = FlowPlugin(
            id="custom",
            name="自定义流程",
            stages=[
                FlowStage(id="s1", agent="continuation"),
                FlowStage(id="s2", agent="checkpoint"),
            ],
        )
        assert len(plugin.stages) == 2
        assert plugin.stages[0].id == "s1"
        assert plugin.stages[1].agent == "checkpoint"

    def test_invalid_ui_mode(self) -> None:
        """非法 ui_mode 校验。"""
        with pytest.raises(ValidationError):
            FlowPlugin(id="test", name="test", ui_mode="invalid")

    def test_invalid_accept_mode(self) -> None:
        """非法 accept_mode 校验。"""
        with pytest.raises(ValidationError):
            FlowPlugin(id="test", name="test", accept_mode="invalid")

    def test_path_traversal_id(self) -> None:
        """ID 含路径字符校验。"""
        for bad_id in ("a/b", "a\\b", "..", "a\x00b"):
            with pytest.raises(ValidationError):
                FlowPlugin(id=bad_id, name="test")

    def test_builtin_plugin_round_trip(self) -> None:
        """内置插件序列化/反序列化一致性。"""
        plugin = FlowPlugin(
            id="single",
            name="单次续写",
            builtin=True,
            stages=[FlowStage(id="write", agent="continuation", created_by="continuation")],
        )
        data = plugin.model_dump(mode="json")
        restored = FlowPlugin.model_validate(data)
        assert restored.id == plugin.id
        assert restored.builtin is True
        assert len(restored.stages) == 1
        assert restored.stages[0].created_by == "continuation"


# ===== 服务 CRUD 测试 =====


class TestFlowPluginService:
    """FlowPluginService 服务测试。"""

    @pytest.fixture
    def service(self, tmp_path: Path) -> FlowPluginService:
        """临时目录初始化服务（首启复制内置插件）。"""
        return FlowPluginService(storage_path=tmp_path)

    def test_builtin_plugins_copied(self, service: FlowPluginService) -> None:
        """首启复制 4 个内置插件。"""
        ids = service.list_ids()
        assert "single" in ids
        assert "volume" in ids
        assert "rewrite_current" in ids
        assert "writing_mode" in ids

    def test_load_builtin_single(self, service: FlowPluginService) -> None:
        """加载内置 single 插件。"""
        plugin = service.load_plugin("single")
        assert plugin is not None
        assert plugin.id == "single"
        assert plugin.builtin is True
        assert plugin.accept_mode == "promote"
        assert len(plugin.stages) == 1
        assert plugin.stages[0].agent == "continuation"

    def test_load_builtin_rewrite_current(self, service: FlowPluginService) -> None:
        """加载内置 rewrite_current 插件（2 阶段）。"""
        plugin = service.load_plugin("rewrite_current")
        assert plugin is not None
        assert plugin.accept_mode == "replace"
        assert len(plugin.stages) == 2
        assert plugin.stages[0].agent == "audit"
        assert plugin.stages[1].agent == "continuation"
        assert plugin.stages[1].input_from == "analysis"

    def test_load_builtin_volume(self, service: FlowPluginService) -> None:
        """加载内置 volume 插件（v2.0，4 阶段 volume_phase）。"""
        plugin = service.load_plugin("volume")
        assert plugin is not None
        assert plugin.ui_mode == "volume"
        assert plugin.accept_mode == "volume_insert"
        assert plugin.version == "2.0"
        assert len(plugin.stages) == 4
        expected_ids = ["deep_analysis", "volume_outline", "outline_audit", "chapter_writing"]
        assert [s.id for s in plugin.stages] == expected_ids
        for stage in plugin.stages:
            assert stage.agent == "volume_phase"
            assert stage.params.get("phase") in expected_ids

    def test_load_builtin_writing_mode(self, service: FlowPluginService) -> None:
        """加载内置 writing_mode 插件（3 阶段 audit→audit→continuation）。"""
        plugin = service.load_plugin("writing_mode")
        assert plugin is not None
        assert plugin.builtin is True
        assert plugin.ui_mode == "standard"
        assert plugin.accept_mode == "promote"
        assert len(plugin.stages) == 3
        # 阶段 1：写作要素分析
        assert plugin.stages[0].id == "analysis"
        assert plugin.stages[0].agent == "audit"
        assert plugin.stages[0].flow_key == "writing_element_analysis"
        assert plugin.stages[0].params.get("phase") == "writing_element_analysis"
        # 阶段 2：写作要素深化
        assert plugin.stages[1].id == "refinement"
        assert plugin.stages[1].agent == "audit"
        assert plugin.stages[1].flow_key == "writing_element_refinement"
        assert plugin.stages[1].input_from == "analysis"
        # 阶段 3：单章生成
        assert plugin.stages[2].id == "generate"
        assert plugin.stages[2].agent == "continuation"
        assert plugin.stages[2].created_by == "writing_mode"
        assert plugin.stages[2].input_from == "refinement"

    def test_save_and_load_custom(self, service: FlowPluginService) -> None:
        """保存并加载自定义插件。"""
        plugin = FlowPlugin(
            id="my_flow",
            name="我的流程",
            description="测试用",
            stages=[FlowStage(id="s1", agent="continuation")],
        )
        service.save_plugin(plugin)
        loaded = service.load_plugin("my_flow")
        assert loaded is not None
        assert loaded.name == "我的流程"
        assert loaded.builtin is False

    def test_delete_custom(self, service: FlowPluginService) -> None:
        """删除自定义插件。"""
        plugin = FlowPlugin(id="temp", name="临时")
        service.save_plugin(plugin)
        assert service.delete_plugin("temp") is True
        assert service.load_plugin("temp") is None

    def test_delete_builtin_fails(self, service: FlowPluginService) -> None:
        """内置插件不可删除。"""
        assert service.delete_plugin("single") is False
        assert service.load_plugin("single") is not None

    def test_list_plugin_ids_sorted(self, service: FlowPluginService) -> None:
        """列表排序：内置在前，自定义按 ID。"""
        custom = FlowPlugin(id="zebra", name="Z")
        service.save_plugin(custom)
        custom2 = FlowPlugin(id="apple", name="A")
        service.save_plugin(custom2)
        ids = service.list_plugin_ids_sorted()
        # 内置 4 个在前（顺序不保证，用集合比较）
        assert set(ids[:4]) == {"single", "volume", "rewrite_current", "writing_mode"}
        # 自定义按 ID 排序
        assert ids[4:] == ["apple", "zebra"]

    def test_builtin_plugin_version_upgrade(
        self, service: FlowPluginService, tmp_path: Path
    ) -> None:
        """内置插件版本升级：旧版 builtin 被资源新版覆盖。"""
        # 写入旧版 v1.0 volume.json（builtin=True，单阶段 volume_pipeline）
        old_data = {
            "id": "volume",
            "name": "卷续写（旧版）",
            "version": "1.0",
            "builtin": True,
            "ui_mode": "volume",
            "accept_mode": "volume_insert",
            "stages": [
                {"id": "volume", "agent": "volume_pipeline", "flow_key": "volume_continuation"}
            ],
        }
        (tmp_path / "flow_plugins" / "volume.json").write_text(
            json.dumps(old_data), encoding="utf-8"
        )
        # 重新初始化 service，触发版本升级
        service2 = FlowPluginService(storage_path=tmp_path)
        plugin = service2.load_plugin("volume")
        assert plugin is not None
        assert plugin.version == "2.0"
        assert len(plugin.stages) == 4
        assert plugin.stages[0].agent == "volume_phase"

    def test_builtin_version_upgrade_skips_non_builtin(
        self, service: FlowPluginService, tmp_path: Path
    ) -> None:
        """非 builtin 插件不被版本升级覆盖。"""
        # 写入旧版 v1.0 volume.json（builtin=False，用户自定义修改）
        old_data = {
            "id": "volume",
            "name": "我的自定义卷续写",
            "version": "1.0",
            "builtin": False,
            "ui_mode": "volume",
            "accept_mode": "volume_insert",
            "stages": [
                {"id": "volume", "agent": "volume_pipeline", "flow_key": "volume_continuation"}
            ],
        }
        (tmp_path / "flow_plugins" / "volume.json").write_text(
            json.dumps(old_data), encoding="utf-8"
        )
        # 重新初始化 service，不升级
        service2 = FlowPluginService(storage_path=tmp_path)
        plugin = service2.load_plugin("volume")
        assert plugin is not None
        assert plugin.version == "1.0"
        assert plugin.name == "我的自定义卷续写"
        assert plugin.builtin is False


# ===== 导入导出测试 =====


class TestFlowPluginImportExport:
    """FlowPluginService 导入导出测试。"""

    @pytest.fixture
    def service(self, tmp_path: Path) -> FlowPluginService:
        """临时目录初始化服务。"""
        return FlowPluginService(storage_path=tmp_path)

    def test_export_plugin(self, service: FlowPluginService, tmp_path: Path) -> None:
        """导出插件为 JSON 文件。"""
        dest = tmp_path / "exported.json"
        assert service.export_plugin("single", dest) is True
        data = json.loads(dest.read_text(encoding="utf-8"))
        assert data["id"] == "single"
        assert data["name"] == "单次续写"

    def test_export_nonexistent(self, service: FlowPluginService, tmp_path: Path) -> None:
        """导出不存在的插件返回 False。"""
        dest = tmp_path / "none.json"
        assert service.export_plugin("nonexistent", dest) is False

    def test_import_plugin(self, service: FlowPluginService, tmp_path: Path) -> None:
        """导入插件 JSON。"""
        src = tmp_path / "custom.json"
        plugin_data = {
            "id": "imported_flow",
            "name": "导入的流程",
            "stages": [{"id": "s1", "agent": "continuation"}],
        }
        src.write_text(json.dumps(plugin_data), encoding="utf-8")
        result = service.import_plugin(src)
        assert result is not None
        assert result.id == "imported_flow"
        assert result.builtin is False
        # 验证已持久化
        loaded = service.load_plugin("imported_flow")
        assert loaded is not None

    def test_import_forces_non_builtin(self, service: FlowPluginService, tmp_path: Path) -> None:
        """导入的插件强制 builtin=False。"""
        src = tmp_path / "fake_builtin.json"
        plugin_data = {
            "id": "fake",
            "name": "伪装内置",
            "builtin": True,
        }
        src.write_text(json.dumps(plugin_data), encoding="utf-8")
        result = service.import_plugin(src)
        assert result is not None
        assert result.builtin is False

    def test_import_id_conflict(self, service: FlowPluginService, tmp_path: Path) -> None:
        """ID 冲突追加 _imported 后缀。"""
        src = tmp_path / "conflict.json"
        plugin_data = {"id": "single", "name": "冲突插件"}
        src.write_text(json.dumps(plugin_data), encoding="utf-8")
        result = service.import_plugin(src)
        assert result is not None
        assert result.id == "single_imported"

    def test_import_overwrite(self, service: FlowPluginService, tmp_path: Path) -> None:
        """overwrite=True 覆盖同名插件。"""
        src = tmp_path / "overwrite.json"
        plugin_data = {"id": "single", "name": "覆盖版本"}
        src.write_text(json.dumps(plugin_data), encoding="utf-8")
        result = service.import_plugin(src, overwrite=True)
        assert result is not None
        assert result.id == "single"
        loaded = service.load_plugin("single")
        assert loaded is not None
        assert loaded.name == "覆盖版本"

    def test_export_then_import_round_trip(
        self, service: FlowPluginService, tmp_path: Path
    ) -> None:
        """导出后重新导入一致性。"""
        # 导出内置插件
        dest = tmp_path / "round_trip.json"
        service.export_plugin("rewrite_current", dest)
        # 修改 ID 后导入（避免冲突）
        data = json.loads(dest.read_text(encoding="utf-8"))
        data["id"] = "rewrite_copy"
        dest.write_text(json.dumps(data), encoding="utf-8")
        result = service.import_plugin(dest)
        assert result is not None
        assert result.id == "rewrite_copy"
        assert len(result.stages) == 2
        assert result.accept_mode == "replace"


# ===== 执行引擎测试 =====


class TestFlowExecutor:
    """FlowExecutor 阶段执行引擎测试。"""

    def test_single_stage_execution(self) -> None:
        """单阶段插件执行。"""
        executor = FlowExecutor()
        results: list[str] = []

        def handler(stage, params, context):
            results.append(stage.id)
            return "done"

        executor.register_handler("continuation", handler)
        plugin = FlowPlugin(
            id="test_single",
            name="test",
            stages=[FlowStage(id="s1", agent="continuation")],
        )
        executor.execute(plugin, {}, {})
        assert results == ["s1"]
        assert not executor.is_active

    def test_multi_stage_execution(self) -> None:
        """多阶段插件顺序执行。"""
        executor = FlowExecutor()
        results: list[str] = []

        def cont_handler(stage, params, context):
            results.append(f"cont:{stage.id}")
            return f"output_{stage.id}"

        def checkpoint_handler(stage, params, context):
            results.append(f"ckpt:{stage.id}")
            return "continue"

        executor.register_handler("continuation", cont_handler)
        executor.register_handler("checkpoint", checkpoint_handler)
        plugin = FlowPlugin(
            id="test_multi",
            name="test",
            stages=[
                FlowStage(id="s1", agent="continuation"),
                FlowStage(id="s2", agent="checkpoint", input_from="s1"),
            ],
        )
        executor.execute(plugin, {}, {})
        assert results == ["cont:s1", "ckpt:s2"]
        assert not executor.is_active

    def test_pending_and_resume(self) -> None:
        """挂起-恢复机制。"""
        executor = FlowExecutor()
        results: list[str] = []

        def audit_handler(stage, params, context):
            results.append("audit")
            return "pending"

        def cont_handler(stage, params, context):
            prev = params.get("_prev_output", "")
            results.append(f"cont:{prev}")
            return None

        executor.register_handler("audit", audit_handler)
        executor.register_handler("continuation", cont_handler)
        plugin = FlowPlugin(
            id="test_pending",
            name="test",
            stages=[
                FlowStage(id="s1", agent="audit"),
                FlowStage(id="s2", agent="continuation", input_from="s1"),
            ],
        )
        executor.execute(plugin, {}, {})
        # 挂起在 s1
        assert results == ["audit"]
        assert executor.is_active
        assert executor.current_stage is not None
        assert executor.current_stage.id == "s1"
        # 恢复
        executor.resume("analysis_text")
        assert results == ["audit", "cont:analysis_text"]
        assert not executor.is_active

    def test_cancel(self) -> None:
        """cancel 清理执行状态。"""
        executor = FlowExecutor()

        def audit_handler(stage, params, context):
            return "pending"

        executor.register_handler("audit", audit_handler)
        plugin = FlowPlugin(
            id="test_cancel",
            name="test",
            stages=[FlowStage(id="s1", agent="audit")],
        )
        executor.execute(plugin, {}, {})
        assert executor.is_active
        executor.cancel()
        assert not executor.is_active
        assert executor.current_stage is None

    def test_cancel_handler(self) -> None:
        """handler 返回 cancel 中断流程。"""
        executor = FlowExecutor()
        results: list[str] = []

        def handler(stage, params, context):
            results.append(stage.id)
            return "cancel"

        executor.register_handler("continuation", handler)
        plugin = FlowPlugin(
            id="test_cancel_handler",
            name="test",
            stages=[
                FlowStage(id="s1", agent="continuation"),
                FlowStage(id="s2", agent="continuation"),
            ],
        )
        executor.execute(plugin, {}, {})
        assert results == ["s1"]  # s2 不执行
        assert not executor.is_active

    def test_unregistered_agent_raises(self) -> None:
        """未注册的 agent 类型抛 ValueError。"""
        executor = FlowExecutor()
        plugin = FlowPlugin(
            id="test_unregistered",
            name="test",
            stages=[FlowStage(id="s1", agent="continuation")],
        )
        with pytest.raises(ValueError, match="未注册的 agent 类型"):
            executor.execute(plugin, {}, {})

    def test_stage_params_merge(self) -> None:
        """阶段 params 覆盖面板 params。"""
        executor = FlowExecutor()
        received_params: dict = {}

        def handler(stage, params, context):
            received_params.update(params)
            return None

        executor.register_handler("continuation", handler)
        plugin = FlowPlugin(
            id="test_params",
            name="test",
            stages=[
                FlowStage(
                    id="s1",
                    agent="continuation",
                    params={"custom": "stage_value", "override": "stage"},
                ),
            ],
        )
        executor.execute(plugin, {"panel": "value", "override": "panel"}, {})
        assert received_params["panel"] == "value"
        assert received_params["custom"] == "stage_value"
        assert received_params["override"] == "stage"  # 阶段覆盖面板

    def test_input_from_passes_prev_output(self) -> None:
        """input_from 传递上一阶段输出到 _prev_output。"""
        executor = FlowExecutor()
        received_prev: list = []

        def s1_handler(stage, params, context):
            return "s1_result"

        def s2_handler(stage, params, context):
            received_prev.append(params.get("_prev_output"))
            return None

        executor.register_handler("continuation", s1_handler)
        executor.register_handler("checkpoint", s2_handler)
        plugin = FlowPlugin(
            id="test_input_from",
            name="test",
            stages=[
                FlowStage(id="s1", agent="continuation"),
                FlowStage(id="s2", agent="checkpoint", input_from="s1"),
            ],
        )
        executor.execute(plugin, {}, {})
        assert received_prev == ["s1_result"]

    def test_volume_phase_pending_and_resume(self) -> None:
        """volume_phase agent 挂起-恢复机制（模拟卷续写多阶段流程）。"""
        executor = FlowExecutor()
        results: list[str] = []

        def volume_phase_handler(stage, params, context):
            phase = stage.params.get("phase", "all")
            results.append(f"phase:{phase}")
            return "pending"  # 每个阶段挂起，等待 resume

        executor.register_handler("volume_phase", volume_phase_handler)
        plugin = FlowPlugin(
            id="test_volume_phase",
            name="test",
            stages=[
                FlowStage(
                    id="deep_analysis",
                    agent="volume_phase",
                    params={"phase": "deep_analysis"},
                ),
                FlowStage(
                    id="volume_outline",
                    agent="volume_phase",
                    params={"phase": "volume_outline"},
                    input_from="deep_analysis",
                ),
                FlowStage(
                    id="chapter_writing",
                    agent="volume_phase",
                    params={"phase": "chapter_writing"},
                    input_from="volume_outline",
                ),
            ],
        )
        executor.execute(plugin, {}, {})
        # 挂起在阶段 1
        assert results == ["phase:deep_analysis"]
        assert executor.is_active
        assert executor.current_stage is not None
        assert executor.current_stage.id == "deep_analysis"
        # 恢复 → 阶段 2 挂起
        executor.resume("deep_analysis_artifact")
        assert results == ["phase:deep_analysis", "phase:volume_outline"]
        assert executor.is_active
        # 恢复 → 阶段 3 挂起
        executor.resume("volume_outline_artifact")
        assert results == [
            "phase:deep_analysis",
            "phase:volume_outline",
            "phase:chapter_writing",
        ]
        assert executor.is_active
        # 恢复 → 流程结束
        executor.resume("chapter_writing_artifact")
        assert not executor.is_active
