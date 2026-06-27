"""大纲与评审报告的序列化/反序列化工具。

提供 Outline / CritiqueReport 与可读 Markdown 文本之间的相互转换，
供 AgentPanel 与 CheckpointDialog 共享，避免重复实现。
"""
from __future__ import annotations

import re

from novelforge.models import CritiqueReport, Outline, Scene


def format_outline(outline: Outline) -> str:
    """将 Outline 转为可读 Markdown 文本。

    Args:
        outline: 大纲对象

    Returns:
        格式化后的 Markdown 文本
    """
    lines: list[str] = ["# 续写大纲"]
    lines.append(f"## 续写目标\n{outline.continuation_goals}")
    lines.append(f"## 伏笔计划\n{outline.foreshadowing_plan}")
    lines.append("## 场景列表")
    for i, scene in enumerate(outline.scenes, 1):
        lines.append(f"### 场景 {i}")
        lines.append(f"- 目的：{scene.purpose}")
        lines.append(f"- 视角：{scene.pov}")
        lines.append(f"- 类型：{scene.scene_type}")
        lines.append(f"- 目标：{scene.goal}")
        lines.append(f"- 冲突：{scene.conflict}")
        lines.append(f"- 结果：{scene.outcome}")
        lines.append(f"- 价值转变：{scene.value_shift}")
        lines.append(f"- 伏笔：{scene.foreshadowing}")
        lines.append(f"- 出场钩子：{scene.exit_hook}")
    return "\n".join(lines)


def format_critique(critique: CritiqueReport) -> str:
    """将 CritiqueReport 转为可读文本。

    Args:
        critique: 评审报告对象

    Returns:
        格式化后的文本
    """
    lines: list[str] = ["# 评审报告"]
    lines.append(f"总结：{critique.summary}")
    lines.append(f"通过：{'是' if critique.passed else '否'}")
    lines.append("## 问题列表")
    for i, issue in enumerate(critique.issues, 1):
        lines.append(f"### 问题 {i} [{issue.severity}] {issue.category}")
        lines.append(f"- 位置：{issue.location}")
        lines.append(f"- 描述：{issue.description}")
        lines.append(f"- 建议：{issue.suggestion}")
    return "\n".join(lines)


def parse_outline(text: str) -> Outline | None:
    """从编辑后的文本解析回 Outline（宽松解析，失败返回 None）。

    使用正则和字符串分割提取各字段，若关键结构缺失则返回 None。

    Args:
        text: 编辑后的 Markdown 文本

    Returns:
        解析后的 Outline 对象，失败时返回 None
    """
    try:
        # 提取续写目标
        continuation_goals = ""
        m = re.search(
            r"## 续写目标\s*\n(.*?)(?=\n## |\Z)", text, re.DOTALL
        )
        if m:
            continuation_goals = m.group(1).strip()

        # 提取伏笔计划
        foreshadowing_plan = ""
        m = re.search(
            r"## 伏笔计划\s*\n(.*?)(?=\n## |\Z)", text, re.DOTALL
        )
        if m:
            foreshadowing_plan = m.group(1).strip()

        # 提取场景列表
        scenes: list[Scene] = []
        scene_blocks = re.split(r"(?=### 场景 \d+)", text)
        for block in scene_blocks:
            if not block.strip().startswith("### 场景"):
                continue
            scene = parse_scene_block(block)
            if scene is not None:
                scenes.append(scene)

        return Outline(
            continuation_goals=continuation_goals,
            foreshadowing_plan=foreshadowing_plan,
            scenes=scenes,
        )
    except Exception:
        return None


def parse_scene_block(block: str) -> Scene | None:
    """从单个场景块解析 Scene 对象。

    Args:
        block: 以 "### 场景" 开头的文本块

    Returns:
        Scene 对象，解析失败返回 None
    """
    field_map = {
        "目的": "purpose",
        "视角": "pov",
        "类型": "scene_type",
        "目标": "goal",
        "冲突": "conflict",
        "结果": "outcome",
        "价值转变": "value_shift",
        "伏笔": "foreshadowing",
        "出场钩子": "exit_hook",
    }
    data: dict[str, str] = {}
    for line in block.split("\n"):
        line = line.strip()
        if not line.startswith("- "):
            continue
        content = line[2:]  # 去掉 "- "
        for cn_name, en_name in field_map.items():
            prefix = f"{cn_name}："
            if content.startswith(prefix):
                data[en_name] = content[len(prefix) :].strip()
                break
    try:
        return Scene(**data)  # type: ignore[arg-type]
    except Exception:
        return None
