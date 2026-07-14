"""文风档案模型（Style Profile）。

基于九维文笔分析框架，提取小说的文笔风格特征为可量化的参数体系，
作为续写/重写时文风一致性约束与审计的基准。
项目级固化（全文提取一次），与 WorldOntology 同级存储。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StyleProfile(BaseModel):
    """文风档案（Style Profile）。

    提取小说的九维文笔风格特征，量化为可复用的参数体系，
    供续写/重写提示词注入与文风一致性审计使用。
    所有字段默认空字典，向后兼容。

    9 大维度（参考 .trae/skills/novel-style-extractor 九维框架）：
    1. language_texture（语言质感）：句长/词汇/修辞/语体类型
    2. narrative_rhythm（叙事节奏）：冲突密度/对话占比/钩子率/信息释放
    3. scene_construction（画面构建）：感官分布/描写粒度/意象类型
    4. character_portrayal（人物塑造）：对话风格/出场方式/情感层次
    5. emotion_engagement（情感调动）：表达方式/情绪曲线/共鸣机制
    6. innovation_signature（创新辨识度）：风格指纹/与同类差异
    7. protagonist_supporting_ratio（主角配角配比）：出场占比/描写方式/配角功能
    8. perspective_usage（视角运用）：视角类型/切换频率/镜头距离
    9. time_transition（时间与过渡）：时间线结构/过渡方式/时间压力
    """

    model_config = ConfigDict(populate_by_name=True)

    # 1. 语言质感（Language Texture）
    language_texture: dict[str, Any] = Field(default_factory=dict)
    # 含：avg_sentence_length（平均句长）/ sentence_length_std（句长标准差）/ short_sentence_ratio（短句占比）/ long_sentence_ratio（长句占比）/ metaphor_density（比喻密度）/ classical_word_ratio（文言词占比）/ colloquial_word_ratio（口语词占比）/ register_type（语体类型）

    # 2. 叙事节奏（Narrative Rhythm）
    narrative_rhythm: dict[str, Any] = Field(default_factory=dict)
    # 含：conflict_density（冲突密度）/ dialogue_ratio（对话占比）/ description_ratio（描写占比）/ chapter_hook_rate（章节钩子率）/ info_release_pattern（信息释放模式）/ scene_switch_frequency（场景切换频率）

    # 3. 画面构建（Scene Construction）
    scene_construction: dict[str, Any] = Field(default_factory=dict)
    # 含：sensory_density（感官描写密度）/ visual_ratio（视觉占比）/ auditory_ratio（听觉占比）/ tactile_ratio（触觉占比）/ detail_granularity（描写粒度分布）/ material_imagery_ratio（物质性意象占比）

    # 4. 人物塑造（Character Portrayal）
    character_portrayal: dict[str, Any] = Field(default_factory=dict)
    # 含：dialogue_avg_length（平均对话长度）/ compound_tag_ratio（复合对话标签占比）/ subtext_density（潜台词密度）/ voice_distinctness（角色语音区分度）/ entrance_style（人物出场方式）

    # 5. 情感调动（Emotion Engagement）
    emotion_engagement: dict[str, Any] = Field(default_factory=dict)
    # 含：expression_style（情感表达方式）/ psychology_density（心理描写密度）/ emotion_burst_spacing（情绪爆发点间距）/ buildup_length（平均铺垫长度）/ resonance_type（共鸣点类型）

    # 6. 创新辨识度（Innovation Signature）
    innovation_signature: dict[str, Any] = Field(default_factory=dict)
    # 含：style_fingerprint（风格指纹）/ genre_innovation（题材创新）/ narrative_innovation（叙事手法创新）/ language_innovation（语言风格创新）

    # 7. 主角配角配比（Protagonist-Supporting Ratio）
    protagonist_supporting_ratio: dict[str, Any] = Field(default_factory=dict)
    # 含：protagonist_presence_ratio（主角出场占比）/ protagonist_dialogue_ratio（主角与配角对话字数比）/ supporting_independent_action_rate（配角独立行动率）/ protagonist_description_distribution（主角描写方式分布）/ role_structure_type（角色结构类型）

    # 8. 视角运用（Perspective Usage）
    perspective_usage: dict[str, Any] = Field(default_factory=dict)
    # 含：perspective_type（视角类型）/ perspective_switch_frequency（视角切换频率）/ main_perspective_ratio（主视角占比）/ inner_activity_ratio（内心活动占比）/ shot_distance_distribution（视角镜头距离分布）/ reader_awareness（读者知情度）

    # 9. 时间与过渡（Time Transition）
    time_transition: dict[str, Any] = Field(default_factory=dict)
    # 含：timeline_structure（时间线结构）/ scene_avg_length（场景平均长度）/ time_jump_frequency（时间跳跃频率）/ chapter_transition_distribution（章节过渡方式分布）/ time_pressure_type（时间压力类型）

    # 提取元数据
    extracted_at: datetime | None = None
    source_chapter_range: tuple[int, int] | None = None  # 提取来源章节区间（闭区间）
