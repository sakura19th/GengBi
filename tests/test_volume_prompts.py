"""卷级多章节续写阶段提示词模板测试。

验证：
- 4 个卷级阶段模板文件存在且非空
- 各模板包含预期占位符
- 深度分析深度参数（analysis_depth/max_analysis_entries）正确注入
- 卷大纲模板含 chapters 长度 == chapter_count 约束
- 审计模板含 7 维度定义
- get_volume_prompt_path 路径正确（合法 phase 返回存在路径，非法 phase 抛 ValueError）
- 宏替换（str.replace）正确填充所有占位符，无残留 {{...}}
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置离屏平台，避免在 CI 环境中需要显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from novelforge.utils.paths import (
    get_agent_prompt_path,
    get_volume_prompt_path,
    load_text_resource,
)


# ===== 1. 模板文件存在性 =====


def test_phase_deep_analysis_template_exists() -> None:
    """深度分析阶段模板文件存在且非空。"""
    path = get_volume_prompt_path("deep_analysis")
    assert path.exists(), f"模板文件不存在: {path}"
    content = load_text_resource(path)
    assert content, "模板内容为空"
    assert "深度分析" in content or "分析" in content


def test_phase_volume_outline_template_exists() -> None:
    """卷大纲阶段模板文件存在且非空。"""
    path = get_volume_prompt_path("volume_outline")
    assert path.exists(), f"模板文件不存在: {path}"
    content = load_text_resource(path)
    assert content, "模板内容为空"
    assert "大纲" in content or "VolumeOutline" in content


def test_phase_outline_audit_template_exists() -> None:
    """大纲审计阶段模板文件存在且非空。"""
    path = get_volume_prompt_path("outline_audit")
    assert path.exists(), f"模板文件不存在: {path}"
    content = load_text_resource(path)
    assert content, "模板内容为空"
    assert "审计" in content or "audit" in content.lower()


def test_phase_chapter_outline_template_exists() -> None:
    """单章细纲阶段模板文件存在且非空。"""
    path = get_volume_prompt_path("chapter_outline")
    assert path.exists(), f"模板文件不存在: {path}"
    content = load_text_resource(path)
    assert content, "模板内容为空"
    assert "细纲" in content or "Outline" in content


# ===== 2. 占位符完整性 =====


def test_phase_deep_analysis_placeholders() -> None:
    """深度分析模板包含预期占位符（含 context_entries/user_input）与 user_directive_analysis 输出字段。"""
    content = load_text_resource(get_volume_prompt_path("deep_analysis"))
    expected = [
        "{{title}}",
        "{{author}}",
        "{{protagonist}}",
        "{{synopsis}}",
        "{{world_setting}}",
        "{{writing_style}}",
        "{{chapters_text}}",
        "{{analysis_depth}}",
        "{{max_analysis_entries}}",
        "{{context_entries}}",
        "{{user_input}}",
    ]
    for ph in expected:
        assert ph in content, f"深度分析模板缺少占位符: {ph}"
    # 已合并为单个 chapters_text，不再含 full/lookback 两占位符
    assert "{{full_chapters_text}}" not in content, (
        "深度分析模板不应再含 {{full_chapters_text}} 占位符"
    )
    assert "{{lookback_chapters_text}}" not in content, (
        "深度分析模板不应再含 {{lookback_chapters_text}} 占位符"
    )
    # 新增 user_directive_analysis 输出字段
    assert "user_directive_analysis" in content, (
        "深度分析模板应含 user_directive_analysis 输出字段"
    )
    # 新增"用户指令优先"原则
    assert "用户指令优先" in content, "深度分析模板应含'用户指令优先'原则"
    # 新增"用户剧情输出需求解析"任务
    assert "用户剧情输出需求解析" in content, (
        "深度分析模板应含'用户剧情输出需求解析'任务"
    )
    # 章节文本说明改为"仅含插入点前 10 章正文"
    assert "仅含插入点前 10 章正文" in content, (
        "深度分析模板章节文本说明应含'仅含插入点前 10 章正文'"
    )


def test_phase_deep_analysis_merge_template_exists() -> None:
    """深度分析【信息汇总】阶段模板文件存在且非空。"""
    path = get_volume_prompt_path("deep_analysis_merge")
    assert path.exists(), f"模板文件不存在: {path}"
    content = load_text_resource(path)
    assert content, "模板内容为空"
    assert "DeepAnalysis" in content or "深度分析" in content or "整合" in content


def test_phase_deep_analysis_merge_placeholders() -> None:
    """深度分析汇总模板包含 {{deep_analysis}} + 6 个 profile 宏 + context_entries/user_input/chapters_text 占位符。"""
    content = load_text_resource(get_volume_prompt_path("deep_analysis_merge"))
    expected = [
        "{{title}}",
        "{{author}}",
        "{{protagonist}}",
        "{{synopsis}}",
        "{{world_setting}}",
        "{{writing_style}}",
        "{{deep_analysis}}",
        "{{context_entries}}",
        "{{user_input}}",
        "{{chapters_text}}",
    ]
    for ph in expected:
        assert ph in content, f"深度分析汇总模板缺少占位符: {ph}"
    # 新增 user_directive_analysis 输出字段
    assert "user_directive_analysis" in content, (
        "深度分析汇总模板应含 user_directive_analysis 输出字段"
    )
    # 新增"用户指令解析整合"任务
    assert "用户指令解析整合" in content, (
        "深度分析汇总模板应含'用户指令解析整合'任务"
    )


def test_phase_volume_outline_placeholders() -> None:
    """卷大纲模板包含预期占位符（含 user_directive_analysis），前文标题为 # 续写点前 10 章正文（含当前章）。"""
    content = load_text_resource(get_volume_prompt_path("volume_outline"))
    expected = [
        "{{deep_analysis}}",
        "{{chapters_text}}",
        "{{user_input}}",
        "{{chapter_count}}",
        "{{target_words_per_chapter}}",
        "{{context_entries}}",
        "{{user_directive_analysis}}",
    ]
    for ph in expected:
        assert ph in content, f"卷大纲模板缺少占位符: {ph}"
    # 前文段落标签应为 # 续写点前 10 章正文（含当前章）
    assert "# 续写点前 10 章正文（含当前章）" in content, (
        "卷大纲模板前文段落标签应为 # 续写点前 10 章正文（含当前章）"
    )
    assert "# 前文章节内容" not in content, (
        "卷大纲模板不应再含 # 前文章节内容 标签"
    )
    # 新增"首要约束：严格遵从用户剧情输出需求"段
    assert "首要约束：严格遵从用户剧情输出需求" in content, (
        "卷大纲模板应含'首要约束：严格遵从用户剧情输出需求'段"
    )
    # 新增"用户指令优先"原则
    assert "用户指令优先" in content, "卷大纲模板应含'用户指令优先'原则"


def test_phase_outline_audit_placeholders() -> None:
    """大纲审计模板包含预期占位符（含 user_directive_analysis）。"""
    content = load_text_resource(get_volume_prompt_path("outline_audit"))
    expected = [
        "{{volume_outline}}",
        "{{audit_dimensions}}",
        "{{previous_chapters_text}}",
        "{{deep_analysis}}",
        "{{round_idx}}",
        "{{total_rounds}}",
        "{{audit_focus}}",
        "{{user_directive_analysis}}",
    ]
    for ph in expected:
        assert ph in content, f"大纲审计模板缺少占位符: {ph}"
    # 新增 user_directive_compliance 维度定义
    assert "user_directive_compliance" in content, (
        "大纲审计模板应含 user_directive_compliance 维度定义"
    )
    # 新增"用户指令遵从性审计"任务
    assert "用户指令遵从性审计" in content, (
        "大纲审计模板应含'用户指令遵从性审计'任务"
    )
    # 新增"用户指令优先"原则
    assert "用户指令优先" in content, "大纲审计模板应含'用户指令优先'原则"


def test_phase_chapter_outline_placeholders() -> None:
    """单章细纲模板包含 7 个预期占位符。"""
    content = load_text_resource(get_volume_prompt_path("chapter_outline"))
    expected = [
        "{{volume_outline}}",
        "{{chapter_plan}}",
        "{{lookback_chapters_text}}",
        "{{previous_chapters_text}}",
        "{{previous_chapter_text}}",
        "{{user_input}}",
        "{{context_entries}}",
    ]
    for ph in expected:
        assert ph in content, f"单章细纲模板缺少占位符: {ph}"


def test_phase_outline_final_placeholders() -> None:
    """终稿大纲模板包含预期占位符（含 user_directive_analysis）与首要约束段。"""
    content = load_text_resource(get_volume_prompt_path("outline_final"))
    expected = [
        "{{original_outline}}",
        "{{audit_report}}",
        "{{previous_chapters_text}}",
        "{{deep_analysis}}",
        "{{user_directive_analysis}}",
        "{{pacing_speed}}",
    ]
    for ph in expected:
        assert ph in content, f"终稿大纲模板缺少占位符: {ph}"
    # 新增"首要约束：严格遵从用户剧情输出需求"段
    assert "首要约束：严格遵从用户剧情输出需求" in content, (
        "终稿大纲模板应含'首要约束：严格遵从用户剧情输出需求'段"
    )


# ===== 3. 深度分析深度参数注入 =====


def test_deep_analysis_depth_param_injection() -> None:
    """替换 {{analysis_depth}} 与 {{max_analysis_entries}} 后文本含正确深度值与条目数。

    模板中含 4 档深度说明（light=5/standard=15/thorough=30/exhaustive=不限），
    str.replace 注入后文本应包含目标深度与条目值。
    """
    template = load_text_resource(get_volume_prompt_path("deep_analysis"))

    # 各深度档位对应的 max_analysis_entries 自动档位
    cases = [
        ("light", "5"),
        ("standard", "15"),
        ("thorough", "30"),
        ("exhaustive", "不限"),
    ]
    for depth, _desc in cases:
        result = template.replace("{{analysis_depth}}", depth).replace(
            "{{max_analysis_entries}}", "0"
        )
        # 注入后的文本必须包含该深度值
        assert depth in result, f"注入后文本缺少深度值: {depth}"
        # 仍应保留各档位说明文字（light=5 条等），便于模型参考
        assert "light" in result
        assert "standard" in result
        assert "thorough" in result
        assert "exhaustive" in result


def test_deep_analysis_max_entries_override() -> None:
    """max_analysis_entries 非 0 时覆盖深度默认上限，注入后文本含该数值。"""
    template = load_text_resource(get_volume_prompt_path("deep_analysis"))
    # 用户指定 max_analysis_entries=20
    result = template.replace("{{analysis_depth}}", "thorough").replace(
        "{{max_analysis_entries}}", "20"
    )
    assert "20" in result, "注入后文本应包含 max_analysis_entries 数值 20"
    # 同时保留 thorough 深度值
    assert "thorough" in result


# ===== 4. 卷大纲章节数校验 =====


def test_volume_outline_chapter_count_constraint() -> None:
    """卷大纲模板含 chapters 长度 == chapter_count 约束文字。"""
    content = load_text_resource(get_volume_prompt_path("volume_outline"))
    # 模板中含"chapters 长度必须等于 chapter_count"或类似约束
    assert (
        "长度必须" in content
        or "严格等于" in content
        or "== chapter_count" in content
        or "必须等于" in content
    ), "卷大纲模板缺少 chapters 长度约束文字"


def test_volume_outline_chapter_count_injection() -> None:
    """替换 {{chapter_count}} 后文本包含正确数值。"""
    template = load_text_resource(get_volume_prompt_path("volume_outline"))
    for n in (2, 3, 5, 10):
        result = template.replace("{{chapter_count}}", str(n))
        assert str(n) in result, f"注入后文本缺少章节数值: {n}"


# ===== 5. 审计维度校验 =====


def test_outline_audit_dimensions_definition() -> None:
    """审计模板含 8 维度定义（consistency/pacing/engagement/structure/coherence/foreshadowing/characters/style）。"""
    content = load_text_resource(get_volume_prompt_path("outline_audit"))
    expected_dimensions = [
        "consistency",
        "pacing",
        "engagement",
        "structure",
        "coherence",
        "foreshadowing",
        "characters",
        "style",
    ]
    for dim in expected_dimensions:
        assert dim in content, f"审计模板缺少维度定义: {dim}"


def test_outline_audit_dimensions_count() -> None:
    """审计模板至少出现 8 个维度关键词（覆盖定义区与字段说明区）。"""
    content = load_text_resource(get_volume_prompt_path("outline_audit"))
    dims = ["consistency", "pacing", "engagement", "structure", "coherence", "foreshadowing", "characters", "style"]
    for d in dims:
        # 每个维度至少出现 1 次
        assert content.count(d) >= 1, f"维度 {d} 在模板中未出现"


# ===== 6. get_volume_prompt_path 路径正确 =====


def test_get_volume_prompt_path_valid_phases() -> None:
    """对 4 个合法 phase 返回存在的文件路径。"""
    for phase in (
        "deep_analysis",
        "volume_outline",
        "outline_audit",
        "chapter_outline",
    ):
        path = get_volume_prompt_path(phase)
        assert path.name == f"phase_{phase}.txt"
        assert path.parent.name == "agent"
        assert path.exists(), f"合法 phase 路径不存在: {phase}"


def test_get_volume_prompt_path_invalid_phase_raises() -> None:
    """非法 phase 抛 ValueError。"""
    with pytest.raises(ValueError):
        get_volume_prompt_path("analysis")  # 单次续写的 phase，非法

    with pytest.raises(ValueError):
        get_volume_prompt_path("unknown")

    with pytest.raises(ValueError):
        get_volume_prompt_path("")


def test_get_volume_prompt_path_filename_pattern() -> None:
    """返回路径文件名遵循 phase_{phase}.txt 模式。"""
    for phase in ("deep_analysis", "volume_outline", "outline_audit", "chapter_outline"):
        path = get_volume_prompt_path(phase)
        assert path.name.startswith("phase_")
        assert path.name.endswith(".txt")


def test_get_agent_prompt_path_verify_revise() -> None:
    """get_agent_prompt_path 对 verify/revise/chapter_rewrite 返回存在的路径（volume 复用）。"""
    for phase in ("verify", "revise", "chapter_rewrite"):
        path = get_agent_prompt_path(phase)
        assert path.name == f"phase_{phase}.txt"
        assert path.parent.name == "agent"
        assert path.exists(), f"路径不存在: {path}"


# ===== 7. 宏替换函数验证 =====


def _apply_macros(template: str, macros: dict[str, str]) -> str:
    """镜像 VolumeOrchestrator._apply_macros 的 str.replace 逻辑。"""
    result = template
    for placeholder, value in macros.items():
        result = result.replace(placeholder, value)
    return result


def _assert_no_placeholders(text: str) -> None:
    """断言文本中无残留 {{...}} 占位符。"""
    residue = re.findall(r"\{\{[^}]+\}\}", text)
    assert not residue, f"存在未替换的占位符: {residue}"


def test_macro_replacement_deep_analysis() -> None:
    """用合成数据替换深度分析模板占位符，验证无残留。"""
    template = load_text_resource(get_volume_prompt_path("deep_analysis"))
    macros = {
        "{{title}}": "星辰大海",
        "{{author}}": "张三",
        "{{protagonist}}": "林晨",
        "{{synopsis}}": "星际冒险故事",
        "{{world_setting}}": "公元 3024 年的银河帝国",
        "{{writing_style}}": "冷峻、短句、悬念密集",
        "{{chapters_text}}": "## 第一章\n\n飞船起飞了。\n\n## 第二章\n\n抵达火星。",
        "{{analysis_depth}}": "thorough",
        "{{max_analysis_entries}}": "30",
        "{{world_ontology}}": '{"existential_topology": {}}',
        "{{protagonist_profile}}": '{"basic_anchors": {}}',
        "{{context_entries}}": "## 人物\n- 主角林晨",
        "{{user_input}}": "请加强主角内心戏",
    }
    result = _apply_macros(template, macros)
    _assert_no_placeholders(result)
    # 验证中文内容正确替换
    assert "星辰大海" in result
    assert "林晨" in result
    assert "公元 3024 年的银河帝国" in result
    assert "飞船起飞了" in result
    assert "抵达火星" in result
    assert "thorough" in result
    assert "30" in result


def test_macro_replacement_volume_outline() -> None:
    """用合成数据替换卷大纲模板占位符，验证无残留。"""
    template = load_text_resource(get_volume_prompt_path("volume_outline"))
    macros = {
        "{{deep_analysis}}": '{"tone": "紧张"}',
        "{{chapters_text}}": "## 第一章\n\n前文内容。",
        "{{user_input}}": "请规划三章节大纲",
        "{{chapter_count}}": "3",
        "{{target_words_per_chapter}}": "2500",
        "{{pacing_speed}}": "medium",
        "{{context_entries}}": "## 人物\n- 主角林晨",
        "{{world_ontology}}": '{"existential_topology": {}}',
        "{{protagonist_profile}}": '{"basic_anchors": {}}',
        "{{user_directive_analysis}}": '{"required_elements": ["宝物"]}',
    }
    result = _apply_macros(template, macros)
    _assert_no_placeholders(result)
    assert '"tone": "紧张"' in result
    assert "前文内容" in result
    assert "请规划三章节大纲" in result
    assert "3" in result
    assert "2500" in result
    assert "medium" in result


def test_macro_replacement_outline_audit() -> None:
    """用合成数据替换大纲审计模板占位符，验证无残留。"""
    template = load_text_resource(get_volume_prompt_path("outline_audit"))
    macros = {
        "{{volume_outline}}": '{"volume_title": "第一卷", "chapter_count": 3}',
        "{{audit_dimensions}}": "consistency, pacing, engagement",
        "{{previous_chapters_text}}": "## 第一章\n\n前文正文。",
        "{{deep_analysis}}": '{"tone": "紧张"}',
        "{{round_idx}}": "1",
        "{{total_rounds}}": "2",
        "{{audit_focus}}": "第3章人物动机不一致",
        "{{world_ontology}}": '{"existential_topology": {}}',
        "{{protagonist_profile}}": '{"basic_anchors": {}}',
        "{{user_directive_analysis}}": '{"required_elements": ["宝物"]}',
    }
    result = _apply_macros(template, macros)
    _assert_no_placeholders(result)
    assert '"volume_title": "第一卷"' in result
    assert "consistency, pacing, engagement" in result
    assert "前文正文" in result
    assert '"tone": "紧张"' in result
    assert "第3章人物动机不一致" in result


def test_macro_replacement_chapter_outline() -> None:
    """用合成数据替换单章细纲模板占位符，验证无残留。"""
    template = load_text_resource(get_volume_prompt_path("chapter_outline"))
    macros = {
        "{{volume_outline}}": '{"volume_title": "第一卷"}',
        "{{chapter_plan}}": '{"index": 1, "title": "第一章"}',
        "{{lookback_chapters_text}}": "## 第一章\n\n前文参考。",
        "{{previous_chapters_text}}": "（无前文）",
        "{{previous_chapter_text}}": "（无上一章）",
        "{{user_input}}": "续写第一章",
        "{{pacing_speed}}": "medium",
        "{{context_entries}}": "## 人物\n- 主角林晨",
        "{{world_ontology}}": '{"existential_topology": {}}',
        "{{protagonist_profile}}": '{"basic_anchors": {}}',
    }
    result = _apply_macros(template, macros)
    _assert_no_placeholders(result)
    assert '"volume_title": "第一卷"' in result
    assert '"index": 1' in result
    assert "前文参考" in result
    assert "（无前文）" in result
    assert "（无上一章）" in result
    assert "续写第一章" in result


def test_macro_replacement_empty_values() -> None:
    """空字符串替换不报错，且无残留占位符。"""
    for phase in (
        "deep_analysis",
        "volume_outline",
        "outline_audit",
        "chapter_outline",
    ):
        template = load_text_resource(get_volume_prompt_path(phase))
        # 找出模板中所有占位符，全部替换为空字符串
        placeholders = re.findall(r"\{\{[^}]+\}\}", template)
        macros = {ph: "" for ph in placeholders}
        result = _apply_macros(template, macros)
        _assert_no_placeholders(result)


def test_templates_contain_json_constraint() -> None:
    """各卷级模板含 JSON 输出约束文字。"""
    for phase in (
        "deep_analysis",
        "volume_outline",
        "outline_audit",
        "chapter_outline",
    ):
        content = load_text_resource(get_volume_prompt_path(phase))
        assert "JSON" in content, f"{phase} 模板缺少 JSON 约束文字"
        # 至少包含一种约束指导
        assert (
            "严格" in content
            or "代码块" in content
            or "markdown" in content.lower()
        ), f"{phase} 模板缺少 JSON 输出格式指导"


# ===== 9. phase_chapter_rewrite.txt 模板测试 =====


def test_phase_chapter_rewrite_template_exists() -> None:
    """phase_chapter_rewrite.txt 模板存在且可加载。"""
    path = get_agent_prompt_path("chapter_rewrite")
    assert path.name == "phase_chapter_rewrite.txt"
    assert path.exists(), f"路径不存在: {path}"
    content = load_text_resource(path)
    assert len(content) > 0


def test_phase_chapter_rewrite_placeholders() -> None:
    """phase_chapter_rewrite.txt 含 10 个占位符且无残留。"""
    template = load_text_resource(get_agent_prompt_path("chapter_rewrite"))
    expected_placeholders = [
        "{{original_content}}",
        "{{critique}}",
        "{{revision_guidance}}",
        "{{chapter_plan}}",
        "{{outline}}",
        "{{previous_chapters_text}}",
        "{{world_ontology}}",
        "{{protagonist_profile}}",
        "{{pacing_speed}}",
        "{{target_words}}",
    ]
    for ph in expected_placeholders:
        assert ph in template, f"phase_chapter_rewrite.txt 缺少占位符: {ph}"
    # 含"重写完整章节"强调语
    assert "重写完整章节" in template
    assert "严禁续写或追加" in template
    # 宏替换无残留
    macros = {ph: "测试值" for ph in expected_placeholders}
    result = _apply_macros(template, macros)
    _assert_no_placeholders(result)


# ===== 10. phase_verify.txt 14 维度与新占位符测试 =====


def test_phase_verify_template_14_dimensions() -> None:
    """phase_verify.txt 含 14 个审计维度定义。"""
    template = load_text_resource(get_agent_prompt_path("verify"))
    # 11 个原有维度
    original_dims = [
        "consistency", "pacing", "engagement", "structure", "coherence",
        "foreshadowing", "characters", "style",
        "protagonist_consistency", "worldview_consistency", "user_directive_compliance",
    ]
    # 3 个新维度
    new_dims = ["outline_alignment", "detail_outline_alignment", "chapter_transition"]
    for dim in original_dims + new_dims:
        assert dim in template, f"phase_verify.txt 缺少维度: {dim}"
    # 含"十四个维度"
    assert "十四个维度" in template


def test_phase_verify_previous_chapter_text_placeholder() -> None:
    """phase_verify.txt 含 {{previous_chapter_text}} 占位符。"""
    template = load_text_resource(get_agent_prompt_path("verify"))
    assert "{{previous_chapter_text}}" in template
    # 含章节衔接审计段说明
    assert "前一章结尾正文" in template
    assert "章节衔接审计" in template


def test_phase_verify_six_summary_markers() -> None:
    """phase_verify.txt summary 含 6 个固定标记段落。"""
    template = load_text_resource(get_agent_prompt_path("verify"))
    markers = [
        "【主角一致性审计】",
        "【世界观一致性审计】",
        "【用户指令遵从性审计】",
        "【大纲一致性审计】",
        "【细纲一致性审计】",
        "【章节衔接审计】",
    ]
    for marker in markers:
        assert marker in template, f"phase_verify.txt 缺少 summary 标记段落: {marker}"
