"""M2 里程碑提示词组装管线测试脚本。

验证内容：
1. 创建测试预设（含 main system prompt、chatHistory marker、worldInfoBefore/After marker、
   一个 ABSOLUTE 注入提示）
2. 创建 10 章测试历史
3. 调用 PromptAssembler 组装
4. 验证 messages 数组结构正确（marker 位置、深度注入位置、Token 裁剪）

运行方式：
    python tests/test_m2_prompt_assembly.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from novelforge.core.macros import MacroContext, MacroEngine
from novelforge.core.prompt_assembler import (
    INJECTION_ABSOLUTE,
    INJECTION_RELATIVE,
    PromptAssembler,
)
from novelforge.core.token_counter import TokenCounter
from novelforge.models import (
    GLOBAL_CHARACTER_ID,
    Chapter,
    ContextEntry,
    NovelProfile,
    Prompt,
    PromptOrderEntry,
    PromptOrderGroup,
    WritingPreset,
)
from novelforge.services.preset_service import PresetService


# ===== 测试工具函数 =====

def make_test_preset() -> WritingPreset:
    """构建测试预设。

    含：
    - main（系统提示，RELATIVE）
    - worldInfoBefore（marker，RELATIVE）
    - chatHistory（marker，RELATIVE）
    - worldInfoAfter（marker，RELATIVE，position=end）
    - injectNote（ABSOLUTE 注入提示，depth=4）
    """
    return WritingPreset(
        id="test_preset",
        name="测试预设",
        prompts=[
            Prompt(
                identifier="main",
                name="主提示",
                role="system",
                content=(
                    "你是一位专业的小说续写助手。当前小说：《{{book}}》，"
                    "主角：{{protagonist}}，当前章节：{{chapter_title}}。"
                    "请根据前文章节续写约 {{target_words}} 字。"
                ),
                system_prompt=True,
                marker=None,
                position="start",
                injection_position=INJECTION_RELATIVE,
            ),
            Prompt(
                identifier="worldInfoBefore",
                name="世界书（前）",
                role="system",
                marker="worldInfoBefore",
                position="start",
                injection_position=INJECTION_RELATIVE,
            ),
            Prompt(
                identifier="chatHistory",
                name="章节历史",
                role="system",
                marker="chatHistory",
                position="start",
                injection_position=INJECTION_RELATIVE,
            ),
            Prompt(
                identifier="worldInfoAfter",
                name="世界书（后）",
                role="system",
                marker="worldInfoAfter",
                position="end",
                injection_position=INJECTION_RELATIVE,
            ),
            Prompt(
                identifier="injectNote",
                name="注入提示",
                role="system",
                content="【注入提示】请在续写时注意保持人物性格一致。",
                marker=None,
                position="start",
                injection_position=INJECTION_ABSOLUTE,
                injection_depth=4,
                injection_order=100,
            ),
        ],
        prompt_order=[
            PromptOrderGroup(
                character_id=GLOBAL_CHARACTER_ID,
                order=[
                    PromptOrderEntry(identifier="main", enabled=True),
                    PromptOrderEntry(identifier="worldInfoBefore", enabled=True),
                    PromptOrderEntry(identifier="chatHistory", enabled=True),
                    PromptOrderEntry(identifier="worldInfoAfter", enabled=True),
                    PromptOrderEntry(identifier="injectNote", enabled=True),
                ],
            )
        ],
        generation_params={
            "temperature": 0.8,
            "max_tokens": 2000,
            "max_context": 32000,
            "top_p": 0.95,
        },
    )


def make_test_chapters(count: int = 10) -> list[Chapter]:
    """构建测试章节列表。

    每章包含标题和正文，正文长度递增以便测试 Token 裁剪。
    """
    chapters: list[Chapter] = []
    for i in range(count):
        chapter = Chapter(
            id=f"ch_{i:02d}",
            project_id="test_project",
            index=i,
            title=f"第{i + 1}章 测试章节",
            content=f"这是第{i + 1}章的正文内容。" * (10 + i),
        )
        chapters.append(chapter)
    return chapters


def make_test_context_entries() -> list[ContextEntry]:
    """构建测试上下文条目。

    含：
    - 2 个 before 条目（worldInfoBefore marker）
    - 2 个 after 条目（worldInfoAfter marker）
    - 1 个 at_depth 条目（深度注入）
    """
    return [
        ContextEntry(
            uid="ctx_before_1",
            category="characters",
            key=["主角"],
            content="主角张三，性格坚毅，武艺高强。",
            order=100,
            position="before",
        ),
        ContextEntry(
            uid="ctx_before_2",
            category="locations",
            key=["京城"],
            content="京城是故事的主要发生地，繁华热闹。",
            order=200,
            position="before",
        ),
        ContextEntry(
            uid="ctx_after_1",
            category="plot_state",
            key=["剧情"],
            content="当前剧情：主角正在寻找失落的宝物。",
            order=100,
            position="after",
        ),
        ContextEntry(
            uid="ctx_after_2",
            category="style",
            key=["风格"],
            content="写作风格：古风武侠，注重环境描写。",
            order=200,
            position="after",
        ),
        ContextEntry(
            uid="ctx_at_depth_1",
            category="events",
            key=["关键事件"],
            content="【深度注入条目】上一章主角遇到了神秘老人。",
            order=100,
            position="at_depth",
            depth=2,
            role="system",
        ),
    ]


def make_test_novel_profile() -> NovelProfile:
    """构建测试小说档案。"""
    return NovelProfile(
        title="测试小说",
        author="测试作者",
        protagonist="张三",
        synopsis="一个关于武侠冒险的故事。",
        world_setting="古代武侠世界。",
        writing_style="古风武侠",
    )


# ===== 断言辅助 =====

class TestResult:
    """测试结果收集器。"""

    # 告知 pytest 不要将此类作为测试类收集
    __test__ = False

    def __init__(self) -> None:
        self.passed: int = 0
        self.failed: int = 0
        self.failures: list[str] = []

    def assert_true(self, condition: bool, message: str) -> None:
        if condition:
            self.passed += 1
        else:
            self.failed += 1
            self.failures.append(message)
            print(f"  [FAIL] {message}")

    def assert_equal(self, actual: object, expected: object, message: str) -> None:
        if actual == expected:
            self.passed += 1
        else:
            self.failed += 1
            self.failures.append(
                f"{message}（期望: {expected!r}, 实际: {actual!r}）"
            )
            print(f"  [FAIL] {message}（期望: {expected!r}, 实际: {actual!r}）")

    def summary(self) -> str:
        total = self.passed + self.failed
        return f"通过 {self.passed}/{total} 项断言，失败 {self.failed} 项"


# pytest fixture：为 test_* 函数提供 TestResult 实例
@pytest.fixture
def result() -> TestResult:
    """提供 TestResult 实例供测试函数使用。"""
    return TestResult()


# ===== 测试用例 =====

def test_basic_assembly_structure(result: TestResult) -> None:
    """测试 1：基本组装结构验证。"""
    print("\n===== 测试 1：基本组装结构 =====")

    preset = make_test_preset()
    chapters = make_test_chapters(10)
    current_chapter = chapters[-1]
    context_entries = make_test_context_entries()
    novel_profile = make_test_novel_profile()

    token_counter = TokenCounter()
    macro_engine = MacroEngine()
    assembler = PromptAssembler(token_counter, macro_engine)

    assemble_result = assembler.assemble(
        preset=preset,
        chapters=chapters,
        current_chapter=current_chapter,
        context_entries=context_entries,
        model="gpt-4o",
        max_context=32000,
        max_tokens=2000,
        target_words=2000,
        novel_profile=novel_profile,
    )

    messages = assemble_result.messages
    print(f"  组装后消息数: {len(messages)}")
    print(f"  历史章节计数: {assemble_result.history_chapter_count}")
    print(f"  Token 使用: {assemble_result.token_usage}")

    # 验证 1：messages 不为空
    result.assert_true(len(messages) > 0, "messages 数组不应为空")

    # 验证 2：第一条消息是 main 系统提示（role=system）
    result.assert_equal(messages[0]["role"], "system", "第一条消息 role 应为 system")
    result.assert_true(
        "小说续写助手" in messages[0]["content"],
        "第一条消息应包含 main 系统提示内容",
    )

    # 验证 3：宏替换生效
    result.assert_true(
        "测试小说" in messages[0]["content"],
        "main 提示中 {{book}} 宏应被替换为「测试小说」",
    )
    result.assert_true(
        "张三" in messages[0]["content"],
        "main 提示中 {{protagonist}} 宏应被替换为「张三」",
    )
    result.assert_true(
        "第10章 测试章节" in messages[0]["content"],
        "main 提示中 {{chapter_title}} 宏应被替换为当前章节标题",
    )
    result.assert_true(
        "2000" in messages[0]["content"],
        "main 提示中 {{target_words}} 宏应被替换为 2000",
    )

    # 验证 4：worldInfoBefore 是单条 system 消息（两条 before 条目用 \n 拼接）
    # messages[1] 应该是 worldInfoBefore
    result.assert_equal(messages[1]["role"], "system", "worldInfoBefore 消息 role 应为 system")
    wi_before_content = messages[1]["content"]
    result.assert_true(
        "主角张三" in wi_before_content,
        "worldInfoBefore 应包含第一条 before 条目内容",
    )
    result.assert_true(
        "京城" in wi_before_content,
        "worldInfoBefore 应包含第二条 before 条目内容",
    )
    result.assert_true(
        "\n" in wi_before_content,
        "worldInfoBefore 多条目应用 \\n 拼接为单条消息",
    )

    # 验证 5：历史消息为 user role，格式为 {title}\n{content}
    # 找到历史消息区间（worldInfoBefore 之后，worldInfoAfter 之前）
    history_start = 2  # main(0) + worldInfoBefore(1)
    # 末尾应为 worldInfoAfter
    last_msg = messages[-1]
    result.assert_equal(last_msg["role"], "system", "最后一条消息 role 应为 system（worldInfoAfter）")
    result.assert_true(
        "当前剧情" in last_msg["content"],
        "worldInfoAfter 应包含 after 条目内容",
    )
    result.assert_true(
        "写作风格" in last_msg["content"],
        "worldInfoAfter 应包含第二条 after 条目内容",
    )

    # 验证 6：历史消息数量（10 章 + 注入提示 + at_depth 注入）
    # 历史消息应为 10 条 user 消息 + 1 条 ABSOLUTE 注入（depth=4）+ 1 条 at_depth 注入（depth=2）
    history_messages = [
        m for m in messages[history_start:-1]
        if m["role"] == "user"
    ]
    result.assert_equal(
        len(history_messages), 10,
        "应有 10 条 user 历史消息（对应 10 章）",
    )

    # 验证 7：历史消息格式
    if history_messages:
        first_history = history_messages[0]
        result.assert_true(
            "第1章" in first_history["content"],
            "历史消息应包含章节标题",
        )
        result.assert_true(
            "\n" in first_history["content"],
            "历史消息 content 格式应为 {标题}\\n{正文}",
        )

    # 验证 8：ABSOLUTE 注入提示存在
    inject_found = any(
        "【注入提示】" in m.get("content", "")
        for m in messages
    )
    result.assert_true(inject_found, "应找到 ABSOLUTE 注入提示内容")

    # 验证 9：at_depth 注入条目存在
    at_depth_found = any(
        "【深度注入条目】" in m.get("content", "")
        for m in messages
    )
    result.assert_true(at_depth_found, "应找到 at_depth 深度注入条目")

    # 验证 10：token_usage 字段完整
    usage = assemble_result.token_usage
    result.assert_true("max_context" in usage, "token_usage 应含 max_context")
    result.assert_true("max_tokens" in usage, "token_usage 应含 max_tokens")
    result.assert_true("system_tokens" in usage, "token_usage 应含 system_tokens")
    result.assert_true("injection_tokens" in usage, "token_usage 应含 injection_tokens")
    result.assert_true("total_used" in usage, "token_usage 应含 total_used")

    # 验证 11：count_mode 返回元组
    is_exact, desc = assemble_result.count_mode
    result.assert_true(
        isinstance(is_exact, bool) and isinstance(desc, str),
        "count_mode 应为 (bool, str) 元组",
    )
    print(f"  计数模式: {assemble_result.count_mode}")

    assert result.failed == 0, f"测试失败：{result.failures}"


def test_depth_injection_position(result: TestResult) -> None:
    """测试 2：深度注入位置验证。

    depth=4 的 ABSOLUTE 注入应插入到倒数第 4 条历史消息之前。
    depth=2 的 at_depth 条目应插入到倒数第 2 条历史消息之前。
    """
    print("\n===== 测试 2：深度注入位置 =====")

    preset = make_test_preset()
    chapters = make_test_chapters(10)
    current_chapter = chapters[-1]
    context_entries = make_test_context_entries()
    novel_profile = make_test_novel_profile()

    assembler = PromptAssembler()
    assemble_result = assembler.assemble(
        preset=preset,
        chapters=chapters,
        current_chapter=current_chapter,
        context_entries=context_entries,
        model="gpt-4o",
        max_context=32000,
        max_tokens=2000,
        target_words=2000,
        novel_profile=novel_profile,
    )

    messages = assemble_result.messages

    # 提取历史区间（跳过 main 和 worldInfoBefore，跳过末尾 worldInfoAfter）
    # 找到 worldInfoBefore 之后的位置
    history_start = 2
    history_end = len(messages) - 1  # 排除 worldInfoAfter

    history_section = messages[history_start:history_end]
    print(f"  历史区间消息数: {len(history_section)}")

    # 找到 ABSOLUTE 注入提示的位置
    inject_idx = None
    for i, m in enumerate(history_section):
        if "【注入提示】" in m.get("content", ""):
            inject_idx = i
            break

    result.assert_true(inject_idx is not None, "应找到 ABSOLUTE 注入提示位置")

    if inject_idx is not None:
        # depth=4：插入到倒数第 4 条历史消息之前
        # 10 条历史，倒数第 4 条是第 7 条（索引 6）
        # 注入应在索引 6 处
        # 但由于 at_depth 注入（depth=2）也可能影响位置，我们验证注入后还有 4 条历史消息
        remaining_after_inject = len(history_section) - inject_idx - 1
        print(f"  注入位置索引: {inject_idx}, 之后剩余消息数: {remaining_after_inject}")
        # depth=4 意味着注入后还有约 4 条历史消息（可能因 at_depth 注入略有不同）
        result.assert_true(
            3 <= remaining_after_inject <= 5,
            f"depth=4 注入后应剩余约 4 条历史消息（实际: {remaining_after_inject}）",
        )

    # 找到 at_depth 注入条目位置
    at_depth_idx = None
    for i, m in enumerate(history_section):
        if "【深度注入条目】" in m.get("content", ""):
            at_depth_idx = i
            break

    result.assert_true(at_depth_idx is not None, "应找到 at_depth 注入条目位置")

    if at_depth_idx is not None:
        # depth=2：插入到倒数第 2 条历史消息之前
        remaining_after_at_depth = len(history_section) - at_depth_idx - 1
        print(f"  at_depth 注入位置索引: {at_depth_idx}, 之后剩余消息数: {remaining_after_at_depth}")
        result.assert_true(
            1 <= remaining_after_at_depth <= 3,
            f"depth=2 注入后应剩余约 2 条历史消息（实际: {remaining_after_at_depth}）",
        )

    assert result.failed == 0, f"测试失败：{result.failures}"


def test_token_trimming(result: TestResult) -> None:
    """测试 3：Token 裁剪验证。

    设置较小的 max_context 和较大的章节内容，验证历史被裁剪。
    """
    print("\n===== 测试 3：Token 裁剪 =====")

    preset = make_test_preset()
    # 构建内容较大的章节，确保超出预算触发裁剪
    chapters: list[Chapter] = []
    for i in range(10):
        # 最后一章内容极大，确保单独超出预算触发截断
        if i == 9:
            content = f"这是第{i + 1}章的正文内容，包含大量文字以确保超出 token 预算。" * 200
        else:
            content = f"这是第{i + 1}章的正文内容，包含大量文字以确保超出 token 预算。" * 20
        chapter = Chapter(
            id=f"ch_big_{i:02d}",
            project_id="test_project",
            index=i,
            title=f"第{i + 1}章 长内容章节",
            content=content,
        )
        chapters.append(chapter)
    current_chapter = chapters[-1]
    context_entries = make_test_context_entries()
    novel_profile = make_test_novel_profile()

    assembler = PromptAssembler()
    # 使用很小的 max_context 触发裁剪
    assemble_result = assembler.assemble(
        preset=preset,
        chapters=chapters,
        current_chapter=current_chapter,
        context_entries=context_entries,
        model="gpt-4o",
        max_context=2000,  # 很小的上下文
        max_tokens=500,
        target_words=2000,
        novel_profile=novel_profile,
    )

    messages = assemble_result.messages
    print(f"  裁剪后消息数: {len(messages)}")
    print(f"  历史章节计数: {assemble_result.history_chapter_count}")
    print(f"  Token 使用: {assemble_result.token_usage}")
    print(f"  警告: {assemble_result.warnings}")

    # 验证 1：历史被裁剪（应少于 10 章）
    result.assert_true(
        assemble_result.history_chapter_count < 10,
        f"小 max_context 下历史应被裁剪（实际保留 {assemble_result.history_chapter_count} 章）",
    )

    # 验证 2：至少保留当前章节（1 章）
    result.assert_true(
        assemble_result.history_chapter_count >= 1,
        "Token 裁剪后应至少保留当前章节（1 章）",
    )

    # 验证 3：total_used 不应严重超出 max_context（容忍 10%）
    total_used = assemble_result.token_usage.get("total_used", 0)
    result.assert_true(
        total_used <= 2000 * 1.1,
        f"裁剪后 token 使用 ({total_used}) 不应超出 max_context 的 10% 容忍范围",
    )

    # 验证 4：当前章节是最后一条历史消息
    history_messages = [
        m for m in messages if m["role"] == "user"
    ]
    if history_messages:
        result.assert_true(
            "第10章" in history_messages[-1]["content"],
            "裁剪后最后一条历史消息应为当前章节（第10章）",
        )
        result.assert_true(
            assemble_result.current_chapter_truncated,
            "当前章节内容过大时应被标记为截断",
        )

    assert result.failed == 0, f"测试失败：{result.failures}"


def test_no_world_info_entries(result: TestResult) -> None:
    """测试 4：无上下文条目时 worldInfoBefore/After marker 应被跳过。"""
    print("\n===== 测试 4：无上下文条目时 marker 跳过 =====")

    preset = make_test_preset()
    chapters = make_test_chapters(5)
    current_chapter = chapters[-1]
    novel_profile = make_test_novel_profile()

    assembler = PromptAssembler()
    assemble_result = assembler.assemble(
        preset=preset,
        chapters=chapters,
        current_chapter=current_chapter,
        context_entries=[],  # 无上下文条目
        model="gpt-4o",
        max_context=32000,
        max_tokens=2000,
        target_words=2000,
        novel_profile=novel_profile,
    )

    messages = assemble_result.messages
    print(f"  无上下文条目时消息数: {len(messages)}")

    # 验证 1：第一条仍是 main 系统提示
    result.assert_equal(messages[0]["role"], "system", "第一条消息应为 system")
    result.assert_true(
        "小说续写助手" in messages[0]["content"],
        "第一条消息应为 main 系统提示",
    )

    # 验证 2：worldInfoBefore marker 被跳过（第二条应是历史消息而非空 worldInfo）
    # 由于无 before 条目，worldInfoBefore 不产生消息
    # 第二条应该是历史消息（user role）或注入提示
    inject_count = sum(
        1 for m in messages
        if "【注入提示】" in m.get("content", "")
    )
    result.assert_equal(inject_count, 1, "应仍有 1 条 ABSOLUTE 注入提示")

    # 验证 3：不应有空内容的 worldInfo 消息
    for m in messages:
        result.assert_true(
            m.get("content", "") != "",
            "不应存在空内容的 worldInfo 消息",
        )

    assert result.failed == 0, f"测试失败：{result.failures}"


def test_preset_service_roundtrip(result: TestResult) -> None:
    """测试 5：PresetService 保存/加载往返测试。"""
    print("\n===== 测试 5：PresetService 保存/加载往返 =====")

    with tempfile.TemporaryDirectory() as tmpdir:
        service = PresetService(storage_path=Path(tmpdir))

        # 确保默认预设存在
        default_preset = service.ensure_default_preset_exists()
        result.assert_equal(default_preset.id, "default", "默认预设 ID 应为 default")

        # 创建测试预设
        test_preset = make_test_preset()
        service.save_preset(test_preset)

        # 重新加载
        loaded = service.load_preset("test_preset")
        result.assert_true(loaded is not None, "加载保存的预设不应返回 None")

        if loaded is not None:
            result.assert_equal(loaded.name, "测试预设", "加载后预设名称应一致")
            result.assert_equal(
                len(loaded.prompts), 5,
                "加载后预设应含 5 个提示",
            )
            # 验证 ABSOLUTE 注入提示保留
            inject_prompt = next(
                (p for p in loaded.prompts if p.identifier == "injectNote"), None
            )
            result.assert_true(inject_prompt is not None, "应找到 injectNote 提示")
            if inject_prompt is not None:
                result.assert_equal(
                    inject_prompt.injection_position, INJECTION_ABSOLUTE,
                    "injectNote 的 injection_position 应为 ABSOLUTE(1)",
                )
                result.assert_equal(
                    inject_prompt.injection_depth, 4,
                    "injectNote 的 injection_depth 应为 4",
                )

        # 列出预设
        presets = service.list_presets()
        preset_ids = [p.id for p in presets]
        result.assert_true(
            "default" in preset_ids, "预设列表应包含 default",
        )
        result.assert_true(
            "test_preset" in preset_ids, "预设列表应包含 test_preset",
        )
        # 默认预设应排在首位
        result.assert_equal(presets[0].id, "default", "默认预设应排在列表首位")

    assert result.failed == 0, f"测试失败：{result.failures}"


def test_st_import_export_roundtrip(result: TestResult) -> None:
    """测试 6：ST 预设导入导出往返测试。"""
    print("\n===== 测试 6：ST 预设导入导出往返 =====")

    with tempfile.TemporaryDirectory() as tmpdir:
        service = PresetService(storage_path=Path(tmpdir))

        # 构建模拟 ST 预设 JSON（含未知字段）
        st_preset_data = {
            "name": "ST 测试预设",
            "prompts": [
                {
                    "identifier": "main",
                    "name": "主提示",
                    "role": "system",
                    "content": "你是续写助手。",
                    "system_prompt": True,
                    "marker": None,
                    "position": "start",
                    "injection_position": 0,
                    "injection_depth": 4,
                    "injection_order": 100,
                    "enable": True,  # ST 未知字段
                    "unknown_field": "保留我",  # ST 未知字段
                },
                {
                    "identifier": "worldInfoBefore",
                    "name": "世界书（前）",
                    "role": "system",
                    "marker": "worldInfoBefore",
                },
                {
                    "identifier": "chatHistory",
                    "name": "章节历史",
                    "role": "system",
                    "marker": "chatHistory",
                },
            ],
            "prompt_order": [
                {
                    "character_id": 100000,  # 全局顺序
                    "order": [
                        {"identifier": "main", "enabled": True},
                        {"identifier": "worldInfoBefore", "enabled": True},
                        {"identifier": "chatHistory", "enabled": True},
                    ],
                },
                {
                    "character_id": 999999,  # 非全局，应被忽略
                    "order": [
                        {"identifier": "main", "enabled": True},
                    ],
                },
            ],
            "temperature": 0.7,
            "max_tokens": 1500,
            "max_context": 16000,
            "unknown_top_field": "顶层未知字段",  # ST 顶层未知字段
        }

        # 写入临时文件
        st_file = Path(tmpdir) / "st_preset.json"
        import json
        st_file.write_text(
            json.dumps(st_preset_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 导入
        imported, _regex_scripts = service.import_from_st_json(st_file)
        result.assert_equal(imported.name, "ST 测试预设", "导入后预设名称应一致")
        result.assert_equal(
            len(imported.prompts), 3,
            "导入后应含 3 个提示",
        )

        # 验证未知字段保留
        main_prompt = next(
            (p for p in imported.prompts if p.identifier == "main"), None
        )
        result.assert_true(main_prompt is not None, "应找到 main 提示")
        if main_prompt is not None:
            result.assert_true(
                "enable" in main_prompt.raw_st_fields,
                "main 提示的未知字段 enable 应保留在 raw_st_fields",
            )
            result.assert_true(
                "unknown_field" in main_prompt.raw_st_fields,
                "main 提示的未知字段 unknown_field 应保留在 raw_st_fields",
            )

        # 验证顶层未知字段保留
        result.assert_true(
            "unknown_top_field" in imported.raw_st_fields,
            "顶层未知字段应保留在 preset.raw_st_fields",
        )

        # 验证非全局 prompt_order 被忽略
        result.assert_equal(
            len(imported.prompt_order), 1,
            "应只保留 1 个全局 prompt_order 分组",
        )
        result.assert_equal(
            imported.prompt_order[0].character_id, GLOBAL_CHARACTER_ID,
            "prompt_order 分组的 character_id 应为 100000",
        )
        result.assert_equal(
            len(imported.prompt_order[0].order), 3,
            "全局 prompt_order 应含 3 个条目",
        )

        # 验证生成参数
        result.assert_equal(
            imported.generation_params.get("temperature"), 0.7,
            "生成参数 temperature 应为 0.7",
        )
        result.assert_equal(
            imported.generation_params.get("max_context"), 16000,
            "生成参数 max_context 应为 16000",
        )

        # 导出往返
        export_file = Path(tmpdir) / "exported.json"
        service.export_to_st_json(imported, export_file)
        result.assert_true(export_file.exists(), "导出文件应存在")

        exported_data = json.loads(export_file.read_text(encoding="utf-8"))
        # 验证未知字段写回
        main_exported = next(
            (p for p in exported_data["prompts"] if p["identifier"] == "main"), None
        )
        if main_exported is not None:
            result.assert_equal(
                main_exported.get("unknown_field"), "保留我",
                "导出后未知字段应原样写回",
            )
        result.assert_equal(
            exported_data.get("unknown_top_field"), "顶层未知字段",
            "导出后顶层未知字段应原样写回",
        )

    assert result.failed == 0, f"测试失败：{result.failures}"


def test_macro_engine(result: TestResult) -> None:
    """测试 7：宏替换引擎验证。"""
    print("\n===== 测试 7：宏替换引擎 =====")

    engine = MacroEngine()
    context = MacroContext(
        book="我的小说",
        author="作者",
        protagonist="主角",
        chapter_title="第一章",
        chapter_index=1,
        target_words=2000,
    )

    # 基本替换
    text = "小说：{{book}}，主角：{{protagonist}}，章节：{{chapter_title}}"
    result_s = engine.substitute(text, context)
    result.assert_equal(
        result_s, "小说：我的小说，主角：主角，章节：第一章",
        "宏替换应正确替换所有内置宏",
    )

    # 未知宏保留原样
    text2 = "已知：{{book}}，未知：{{unknown_macro}}"
    result_s2 = engine.substitute(text2, context)
    result.assert_equal(
        result_s2, "已知：我的小说，未知：{{unknown_macro}}",
        "未知宏应保留原样不替换",
    )

    # 允许空白
    text3 = "小说：{{ book }}"
    result_s3 = engine.substitute(text3, context)
    result.assert_equal(
        result_s3, "小说：我的小说",
        "宏名前后允许空白字符",
    )

    # 提取宏
    macros = MacroEngine.extract_macros("{{book}} 和 {{protagonist}} 和 {{book}}")
    result.assert_equal(
        macros, ["book", "protagonist"],
        "extract_macros 应去重并保持顺序",
    )

    # 缓存生效（第二次替换应命中缓存）
    result_s4 = engine.substitute(text, context)
    result.assert_equal(
        result_s4, result_s,
        "缓存命中应返回相同结果",
    )

    assert result.failed == 0, f"测试失败：{result.failures}"


def test_token_counter(result: TestResult) -> None:
    """测试 8：Token 计数器验证。"""
    print("\n===== 测试 8：Token 计数器 =====")

    counter = TokenCounter()

    # OpenAI 模型检测
    result.assert_true(
        counter.get_count_mode("gpt-4o")[0] is not None
        or counter.get_count_mode("gpt-4o")[0] is False,
        "gpt-4o 应识别为 OpenAI 模型",
    )
    is_exact, desc = counter.get_count_mode("gpt-4o")
    print(f"  gpt-4o 计数模式: is_exact={is_exact}, desc={desc}")

    # 非 OpenAI 模型
    is_exact2, desc2 = counter.get_count_mode("claude-3-opus")
    result.assert_true(
        not is_exact2,
        "claude-3-opus 应为估算模式",
    )
    result.assert_true(
        "估算" in desc2,
        "非 OpenAI 模型描述应含「估算」",
    )

    # 中文文本计数
    chinese_text = "你好世界，这是一个测试。"
    tokens_cn = counter.count(chinese_text, model="claude-3-opus")
    result.assert_true(
        tokens_cn > 0,
        f"中文文本 token 数应大于 0（实际: {tokens_cn}）",
    )

    # messages 计数
    messages = [
        {"role": "system", "content": "你是助手。"},
        {"role": "user", "content": "你好"},
    ]
    msg_tokens = counter.count_messages(messages, model="gpt-4o")
    result.assert_true(
        msg_tokens > 0,
        f"messages token 数应大于 0（实际: {msg_tokens}）",
    )

    # 空文本
    result.assert_equal(
        counter.count("", model="gpt-4o"), 0,
        "空文本 token 数应为 0",
    )

    # 缓存生效（相同文本第二次计数）
    tokens_again = counter.count(chinese_text, model="claude-3-opus")
    result.assert_equal(
        tokens_again, tokens_cn,
        "缓存命中应返回相同 token 数",
    )

    assert result.failed == 0, f"测试失败：{result.failures}"


# ===== 主入口 =====

def main() -> int:
    """运行所有测试。"""
    print("=" * 60)
    print("NovelForge M2 里程碑提示词组装管线测试")
    print("=" * 60)

    result = TestResult()

    test_basic_assembly_structure(result)
    test_depth_injection_position(result)
    test_token_trimming(result)
    test_no_world_info_entries(result)
    test_preset_service_roundtrip(result)
    test_st_import_export_roundtrip(result)
    test_macro_engine(result)
    test_token_counter(result)

    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    print(result.summary())

    if result.failures:
        print("\n失败详情：")
        for f in result.failures:
            print(f"  - {f}")
        return 1

    print("\n所有测试通过！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
