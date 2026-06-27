"""TGbreak 预设端到端测试。

验证使用 TGbreak😺V3.1.1 预设的提示词与正则能正确处理实际小说生成场景，
最终输出纯文本内容。

测试链路：
1. 导入 TGbreak 预设（122 提示 + 13 正则脚本）
2. 导入实际小说《穿进赛博游戏后干掉BOSS成功上位》
3. 用 PromptAssembler 组装 messages（验证 ST 宏解析）
4. 验证 user_input 注入到 messages 末尾
5. 模拟 AI 输出含 TGbreak 标记的文本
6. 应用 AI_OUTPUT 正则脚本
7. 调用 strip_html_tags 剥离 HTML
8. 断言最终输出为纯文本

运行方式：
    python -m pytest tests/test_tgbreak_e2e.py -v
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

# 测试用真实文件路径（位于项目根目录，避免硬编码 Linux 路径导致 Windows/CI 跳过）
TGBREAK_PRESET_PATH = PROJECT_ROOT / "TGbreak😺V3.1.1.json"
NOVEL_TXT_PATH = PROJECT_ROOT / "穿进赛博游戏后干掉BOSS成功上位.txt"

from novelforge.core.prompt_assembler import PromptAssembler
from novelforge.core.regex_engine import RegexEngine, strip_html_tags
from novelforge.core.token_counter import TokenCounter
from novelforge.core.macros import MacroEngine
from novelforge.core.template_engine import TemplateEngine
from novelforge.core.variable_store import VariableStore
from novelforge.models import NovelProfile
from novelforge.models.regex import PLACEMENT_AI_OUTPUT, PLACEMENT_USER_INPUT
from novelforge.services.importer import TxtImporter
from novelforge.services.preset_service import PresetService
from novelforge.services.regex_service import (
    RegexService,
    _parse_regex_script_from_st,
    _generate_regex_id,
)
from novelforge.services.storage_service import StorageService


# ===== Fixtures =====


@pytest.fixture
def temp_storage(tmp_path: Path) -> StorageService:
    """临时存储服务。"""
    return StorageService(storage_path=tmp_path)


@pytest.fixture
def preset_service(tmp_path: Path) -> PresetService:
    """预设服务。"""
    return PresetService(tmp_path)


@pytest.fixture
def regex_service(tmp_path: Path) -> RegexService:
    """正则服务。"""
    return RegexService(tmp_path)


@pytest.fixture
def tgbreak_preset(preset_service: PresetService, regex_service: RegexService):
    """导入 TGbreak 预设及其正则脚本。

    返回 (WritingPreset, list[RegexScript]) 元组。
    """
    if not TGBREAK_PRESET_PATH.exists():
        pytest.skip(f"TGbreak 预设文件不存在: {TGBREAK_PRESET_PATH}")

    preset, regex_scripts_data = preset_service.import_from_st_json(
        str(TGBREAK_PRESET_PATH)
    )

    # 导入正则脚本到 preset 作用域
    scripts: list = []
    for script_data in regex_scripts_data:
        try:
            script = _parse_regex_script_from_st(script_data)
            if not script.id:
                script.id = _generate_regex_id()
            regex_service.add_script(script, scope="preset", preset_id=preset.id)
            scripts.append(script)
        except Exception as e:
            logger.warning("导入正则脚本失败: %s", e)

    return preset, scripts


@pytest.fixture
def novel_chapters(temp_storage: StorageService):
    """导入实际小说并返回 (project, chapters)。"""
    if not NOVEL_TXT_PATH.exists():
        pytest.skip(f"小说文件不存在: {NOVEL_TXT_PATH}")

    importer = TxtImporter(temp_storage)
    result = importer.import_file(str(NOVEL_TXT_PATH))
    return result.project, result.chapters


# ===== 测试用例 =====


class TestTGbreakImport:
    """测试 TGbreak 预设导入。"""

    def test_preset_imported_with_prompts(self, tgbreak_preset) -> None:
        """TGbreak 预设应正确导入 122 个提示。"""
        preset, _ = tgbreak_preset
        assert preset is not None
        assert len(preset.prompts) >= 100, (
            f"预期至少 100 个提示，实际 {len(preset.prompts)}"
        )

    def test_regex_scripts_imported(self, tgbreak_preset) -> None:
        """TGbreak 预设应导入 13 条正则脚本。"""
        _, scripts = tgbreak_preset
        assert len(scripts) >= 10, (
            f"预期至少 10 条正则脚本，实际 {len(scripts)}"
        )

    def test_prompt_order_has_global_group(self, tgbreak_preset) -> None:
        """预设应包含全局 prompt_order 分组。"""
        preset, _ = tgbreak_preset
        has_global = any(
            g.character_id == 100000 for g in preset.prompt_order
        )
        assert has_global, "预设缺少全局 prompt_order 分组（character_id=100000）"

    def test_markers_present(self, tgbreak_preset) -> None:
        """预设应包含 chatHistory/worldInfoBefore/worldInfoAfter marker。"""
        preset, _ = tgbreak_preset
        markers = {p.marker for p in preset.prompts if p.marker}
        assert "chatHistory" in markers, "缺少 chatHistory marker"
        assert "worldInfoBefore" in markers, "缺少 worldInfoBefore marker"
        assert "worldInfoAfter" in markers, "缺少 worldInfoAfter marker"


class TestTGbreakPromptAssembly:
    """测试使用 TGbreak 预设组装提示词。"""

    def test_assemble_with_real_novel(
        self, tgbreak_preset, novel_chapters
    ) -> None:
        """使用 TGbreak 预设对实际小说组装提示词应生成有效 messages。"""
        preset, _ = tgbreak_preset
        project, chapters = novel_chapters

        # 取前 3 章作为上下文
        sorted_chapters = sorted(chapters, key=lambda c: c.index)
        test_chapters = sorted_chapters[:3]
        current = test_chapters[-1] if test_chapters else None
        assert current is not None, "小说无章节"

        token_counter = TokenCounter()
        macro_engine = MacroEngine()
        variable_store = VariableStore()
        template_engine = TemplateEngine(variable_store=variable_store)
        assembler = PromptAssembler(
            token_counter,
            macro_engine,
            regex_engine=RegexEngine(),
            template_engine=template_engine,
        )

        novel_profile = NovelProfile(
            title="穿进赛博游戏后干掉BOSS成功上位",
            author="桉柏",
            protagonist="隗辛",
            synopsis="赛博朋克世界中的卧底冒险故事",
            world_setting="赛博朋克+克系元素的全息游戏世界",
            writing_style="赛博朋克",
        )

        result = assembler.assemble(
            preset=preset,
            chapters=test_chapters,
            current_chapter=current,
            model="gpt-4o",
            max_context=32000,
            max_tokens=2000,
            target_words=2000,
            novel_profile=novel_profile,
            project_id=project.id,
            chapter_metadata={},
        )

        # messages 应非空
        assert len(result.messages) > 0, "组装后 messages 为空"
        # 应包含 system 角色
        roles = {m.get("role") for m in result.messages}
        assert "system" in roles, f"messages 缺少 system 角色: {roles}"

    def test_user_input_appended_to_messages(
        self, tgbreak_preset, novel_chapters
    ) -> None:
        """user_input 应作为最后一条 user 消息追加到 messages 末尾。"""
        preset, _ = tgbreak_preset
        project, chapters = novel_chapters

        sorted_chapters = sorted(chapters, key=lambda c: c.index)
        test_chapters = sorted_chapters[:2]
        current = test_chapters[-1] if test_chapters else None

        token_counter = TokenCounter()
        assembler = PromptAssembler(token_counter, MacroEngine())

        user_instruction = "聚焦主角隗辛的心理变化，增加赛博朋克环境描写"
        result = assembler.assemble(
            preset=preset,
            chapters=test_chapters,
            current_chapter=current,
            novel_profile=NovelProfile(title="测试", protagonist="隗辛"),
            project_id=project.id,
            user_input=user_instruction,
        )

        # 最后一条消息应为 user 角色，内容为 user_input
        last_msg = result.messages[-1]
        assert last_msg["role"] == "user", (
            f"最后一条消息角色应为 user，实际 {last_msg['role']}"
        )
        assert user_instruction in last_msg["content"], (
            "user_input 内容未出现在最后一条消息中"
        )

    def test_no_user_input_when_empty(
        self, tgbreak_preset, novel_chapters
    ) -> None:
        """user_input 为空时不应追加额外消息。"""
        preset, _ = tgbreak_preset
        project, chapters = novel_chapters

        sorted_chapters = sorted(chapters, key=lambda c: c.index)
        test_chapters = sorted_chapters[:2]
        current = test_chapters[-1] if test_chapters else None

        assembler = PromptAssembler(TokenCounter(), MacroEngine())
        result_with = assembler.assemble(
            preset=preset,
            chapters=test_chapters,
            current_chapter=current,
            novel_profile=NovelProfile(title="测试"),
            project_id=project.id,
            user_input="",
        )
        result_without = assembler.assemble(
            preset=preset,
            chapters=test_chapters,
            current_chapter=current,
            novel_profile=NovelProfile(title="测试"),
            project_id=project.id,
        )

        # 两者消息数应相同（空 user_input 不追加）
        assert len(result_with.messages) == len(result_without.messages), (
            "空 user_input 不应追加额外消息"
        )


class TestTGbreakRegexAndPlainText:
    """测试 TGbreak 正则处理与纯文本输出。"""

    def test_regex_scripts_apply_to_ai_output(self, tgbreak_preset) -> None:
        """AI_OUTPUT 正则脚本应能处理含 TGbreak 标记的文本。"""
        _, scripts = tgbreak_preset

        # 编译正则引擎
        engine = RegexEngine()
        ai_output_scripts = [s for s in scripts if PLACEMENT_AI_OUTPUT in s.placement]
        engine.compile_scripts(ai_output_scripts)

        assert len(ai_output_scripts) > 0, "无 AI_OUTPUT 正则脚本"

        # 模拟 AI 输出（含 TGbreak 标记）
        ai_output = (
            "<draft_notes>\n"
            "梳理：主角隗辛进入游戏，发现身份是通缉犯。\n"
            "</draft_notes>\n"
            "<!-- 2.正文前的格式 -->\n"
            "隗辛睁开眼，霓虹灯的光芒刺入瞳孔。\n"
            "她环顾四周，钢铁森林中弥漫着机油与臭氧的气味。\n"
            "<w2g>选项A：潜入数据库 / 选项B：正面突破</w2g>\n"
            "<VariableCheck>变量检查：sanity=85</VariableCheck>\n"
            "<!-- 这是一条注释 -->\n"
            "她握紧拳头，决心干掉BOSS上位。\n"
        )

        # 应用 AI_OUTPUT 正则
        processed = engine.apply_to_text(ai_output, placement=PLACEMENT_AI_OUTPUT)

        # 正则确实处理了文本（处理后与原文本不同）
        assert processed != ai_output, "正则未对文本产生任何变化"
        # trimStrings 会剥离 <，但正则生成的标签会留下 > 或标签名碎片
        assert len(processed) > 0, (
            f"正则处理后输出不应为空，实际: {processed!r}"
        )
        assert ">" in processed or "ai_last_output" in processed, (
            f"正则应用后应含标签碎片（> 或 ai_last_output），实际处理结果: {processed[:200]!r}"
        )

    def test_strip_html_produces_plain_text(self, tgbreak_preset) -> None:
        """strip_html_tags 应将正则处理后的 HTML 转为纯文本。"""
        _, scripts = tgbreak_preset

        engine = RegexEngine()
        ai_output_scripts = [s for s in scripts if PLACEMENT_AI_OUTPUT in s.placement]
        engine.compile_scripts(ai_output_scripts)

        ai_output = (
            "<draft_notes>\n"
            "梳理：主角进入赛博世界。\n"
            "</draft_notes>\n"
            "<!-- 2.正文前的格式 -->\n"
            "霓虹灯下，隗辛快步穿过巷道。\n"
            "<w2g>选项A：潜入 / 选项B：突围</w2g>\n"
            "<Disclaimer>免责声明内容</Disclaimer>\n"
            "<!-- 隐藏注释 -->\n"
            "她拔出数据芯片，插入终端。\n"
        )

        # 完整链路：正则 → strip_html_tags
        processed = engine.apply_to_text(ai_output, placement=PLACEMENT_AI_OUTPUT)
        plain_text = strip_html_tags(processed)

        # 纯文本断言
        assert "<" not in plain_text, (
            f"剥离后仍含 < 标签: {plain_text[:200]}"
        )
        assert ">" not in plain_text, (
            f"剥离后仍含 > 标签: {plain_text[:200]}"
        )
        # 应保留正文中文内容
        assert "霓虹灯" in plain_text or "隗辛" in plain_text, (
            f"纯文本丢失正文内容: {plain_text[:200]}"
        )

    def test_full_pipeline_produces_readable_text(self, tgbreak_preset) -> None:
        """完整链路（正则 + HTML 剥离）应输出可读纯文本。"""
        _, scripts = tgbreak_preset

        engine = RegexEngine()
        ai_output_scripts = [s for s in scripts if PLACEMENT_AI_OUTPUT in s.placement]
        engine.compile_scripts(ai_output_scripts)

        # 模拟更完整的 AI 输出
        ai_output = (
            "<draft_notes>\n"
            "本章梳理：\n"
            "1. 隗辛登录游戏\n"
            "2. 发现通缉犯身份\n"
            "3. 决定卧底策略\n"
            "</draft_notes>\n"
            "<!-- 2.正文前的格式 -->\n"
            "第一章 风起\n\n"
            "隗辛睁开双眼，入目是一片猩红的霓虹。\n"
            "全息投影在空中交织出《深红之土》的标志。\n"
            "她低头看向自己的手——机械义肢泛着冷光。\n"
            "<w2g>\n选项A：检查状态面板\n选项B：观察周围环境\n</w2g>\n"
            "<VariableCheck>当前SAN值：85/100</VariableCheck>\n"
            "<!-- 系统备注：此处为伏笔 -->\n"
            '"这就是……赛博游戏？"她喃喃自语。\n'
        )

        processed = engine.apply_to_text(ai_output, placement=PLACEMENT_AI_OUTPUT)
        plain_text = strip_html_tags(processed)

        # 核心断言：输出为纯文本
        assert "<" not in plain_text, "输出含 HTML 标签"
        assert ">" not in plain_text, "输出含 HTML 标签"
        assert "&nbsp;" not in plain_text, "输出含 HTML 实体"
        assert "&lt;" not in plain_text, "输出含 HTML 实体"

        # 应含正文关键内容
        assert "隗辛" in plain_text, "纯文本丢失主角名"
        assert "霓虹" in plain_text or "赛博" in plain_text, (
            "纯文本丢失正文关键词"
        )

        # 不应残留 TGbreak 标记标签
        for tag in ("<draft_notes>", "</draft_notes>", "<w2g>", "</w2g>",
                    "<VariableCheck>", "</VariableCheck>", "<Disclaimer>"):
            assert tag not in plain_text, f"纯文本残留标签: {tag}"


class TestPresetToggle:
    """测试预设与提示词开关功能。"""

    def test_set_preset_enabled_false(self, preset_service, tgbreak_preset) -> None:
        """set_preset_enabled 应能禁用非默认预设。"""
        preset, _ = tgbreak_preset

        # 禁用预设
        ok = preset_service.set_preset_enabled(preset.id, False)
        assert ok, "禁用预设失败"

        reloaded = preset_service.load_preset(preset.id)
        assert reloaded is not None
        assert reloaded.enabled is False, "预设禁用状态未持久化"

    def test_set_preset_enabled_true(self, preset_service, tgbreak_preset) -> None:
        """set_preset_enabled 应能重新启用预设。"""
        preset, _ = tgbreak_preset

        preset_service.set_preset_enabled(preset.id, False)
        ok = preset_service.set_preset_enabled(preset.id, True)
        assert ok, "启用预设失败"

        reloaded = preset_service.load_preset(preset.id)
        assert reloaded.enabled is True, "预设启用状态未持久化"

    def test_default_preset_cannot_be_disabled(self, preset_service) -> None:
        """默认预设不允许禁用。"""
        preset_service.ensure_default_preset_exists()
        ok = preset_service.set_preset_enabled("default", False)
        assert ok is False, "默认预设不应允许禁用"

    def test_set_prompt_enabled_toggles(self, preset_service, tgbreak_preset) -> None:
        """set_prompt_enabled 应能切换提示词启用状态。"""
        preset, _ = tgbreak_preset

        # 找一个非 marker、非 system_prompt 的提示
        target = None
        for p in preset.prompts:
            if not p.marker and not p.system_prompt:
                target = p
                break

        if target is None:
            pytest.skip("无非 marker/非 system_prompt 提示可供测试")

        # 禁用
        preset_service.set_prompt_enabled(preset, target.identifier, False)
        preset_service.save_preset(preset)

        reloaded = preset_service.load_preset(preset.id)
        prompt_map = {p.identifier: p for p in reloaded.prompts}
        assert prompt_map[target.identifier].enabled is False, (
            "提示词禁用状态未生效"
        )

        # 检查 prompt_order 同步
        for group in reloaded.prompt_order:
            for entry in group.order:
                if entry.identifier == target.identifier:
                    assert entry.enabled is False, (
                        "prompt_order 未同步禁用状态"
                    )
