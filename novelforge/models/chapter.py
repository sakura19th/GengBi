"""章节与续写版本数据模型。

定义 ``Continuation``（swipe）与 ``Chapter``。
字段语义参见 spec.md「多版本（Swipe）管理」与「Chapter 数据结构」一节。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from novelforge.models.context import ContextEntry
from novelforge.models.protagonist import ProtagonistProfile
from novelforge.models.volume import VolumeArtifacts


class Continuation(BaseModel):
    """续写版本（链式续写节点）。

    记录单次续写的完整快照，含参数、预设、正则、上下文、提示词等。
    字段说明：
    - ``is_accepted``：是否被接受（接受后作为链上节点出现在章节列表），默认 false
    - ``parent_id``：父续写 id（None=直接挂在章节下；否则挂在另一续写下，形成链）
    - ``status``：完成状态（completed/interrupted/failed）
    - ``created_by``：创建方式（continuation=续写/rewrite=重写）
    - ``parameters_snapshot``：生成参数快照（temperature/max_tokens 等）
    - ``preset_snapshot``：预设内容副本
    - ``regex_script_ids_snapshot``：正则脚本 ID 列表快照
    - ``extracted_context_snapshot``：上下文提取条目快照
    - ``prompt_snapshot``：发送给 LLM 的 messages 数组快照
    - ``reasoning_content``：推理内容（DeepSeek/xAI 等），不参与后续提示词组装
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    created_at: datetime = Field(default_factory=datetime.now)
    content: str = ""
    model: str = ""
    is_accepted: bool = False
    parent_id: str | None = None
    status: str = "completed"
    created_by: str = "continuation"
    parameters_snapshot: dict[str, Any] = Field(default_factory=dict)
    preset_id: str = ""
    preset_snapshot: dict[str, Any] = Field(default_factory=dict)
    regex_script_ids_snapshot: list[str] = Field(default_factory=list)
    extracted_context_snapshot: list[ContextEntry] = Field(default_factory=list)
    prompt_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    reasoning_content: str | None = None
    generated_title: str = ""  # LLM 生成的章节标题（从 <novelforge_title> 标签提取）
    agent_artifacts: dict[str, Any] | None = None
    volume_artifacts: VolumeArtifacts | None = None
    highlights: list[dict[str, Any]] = Field(default_factory=list)  # 输出栏高亮：[{start, end, color, note}]


class Chapter(BaseModel):
    """章节。

    字段说明：
    - ``index``：章节序号，从 0 开始
    - ``content``：正文（存文件系统，内存中按需加载）
    - ``word_count``：字数
    - ``continuations``：续写节点列表（链式模型，未接受的不在章节列表显示）
    - ``metadata``：元数据（notes、tags 等）

    续写采用链式模型：续写内容存于 ``Continuation.content``，不合并到
    ``chapter.content``；``Continuation.parent_id`` 形成续写链
    （None=章节直接子节点，否则挂在另一续写下）。

    章节正文作为消息注入 chatHistory 时，role 统一为 user，
    content 格式为 ``{章节标题}\\n{章节正文}``。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    project_id: str
    index: int
    title: str = ""
    content: str = ""
    word_count: int = 0
    continuations: list[Continuation] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    protagonist_profile: ProtagonistProfile | None = None

    @field_validator("id", "project_id")
    @classmethod
    def _validate_path_id(cls, v: str) -> str:
        """防御性校验：拒绝含路径字符的 ID，防止导入恶意数据时路径穿越。"""
        if v and ("/" in v or "\\" in v or ".." in v or "\x00" in v):
            raise ValueError(f"非法 ID（含路径字符）: {v!r}")
        return v
