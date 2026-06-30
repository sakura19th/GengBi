"""底层世界观元描述模型（World Ontology Layer）。

参考 novel_continuation_global_framework.md 第一大节「底层世界观元描述」，
7 大维度参数化描述世界运行的元规则（不包含任何具体实体），
作为所有上层设定（魔法体系/社会结构/历史事件）的底层操作系统。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorldOntology(BaseModel):
    """底层世界观元描述（World Ontology Layer）。

    描述虚构世界在最底层如何运行，不涉及任何具体实体，
    只描述世界本身的运行律、存在结构和认知框架。
    所有字段默认空字典，向后兼容。

    7 大维度（参考 novel_continuation_global_framework.md）：
    1. existential_topology（存在拓扑）：存在层级/个体性拓扑/实在性状态
    2. causal_architecture（因果架构）：因果方向性/决定论谱系/概率生态/因果延迟
    3. spatio_temporal_ontology（时空本体论）：时间/空间/时空耦合
    4. information_epistemology（信息与认识论）：信息本体论/真理机制/秘密生态/语言本体论
    5. axiological_foundation（价值论基础）：道德来源/意义系统/审美规则/价值交换
    6. becoming_dynamics（生成动力学）：变化本体论/演化规则/熵与秩序/转化规则
    7. narrative_ontology（叙事本体论）：故事-世界关系/元叙事规则/结局逻辑
    """

    model_config = ConfigDict(populate_by_name=True)

    # 1. 存在拓扑（Existential Topology）
    existential_topology: dict[str, Any] = Field(default_factory=dict)
    # 含：being_hierarchy（存在层级）/ individuality_topology（个体性拓扑）/ reality_status（实在性状态）

    # 2. 因果架构（Causal Architecture）
    causal_architecture: dict[str, Any] = Field(default_factory=dict)
    # 含：causal_directionality（因果方向性）/ determinism_spectrum（决定论谱系）/ probability_ecology（概率生态）/ causal_latency（因果延迟）

    # 3. 时空本体论（Spatio-Temporal Ontology）
    spatio_temporal_ontology: dict[str, Any] = Field(default_factory=dict)
    # 含：time_ontology（时间本体论）/ space_ontology（空间本体论）/ space_time_coupling（时空耦合）

    # 4. 信息与认识论（Information Epistemology）
    information_epistemology: dict[str, Any] = Field(default_factory=dict)
    # 含：information_ontology（信息本体论）/ truth_mechanics（真理机制）/ secret_ecology（秘密生态）/ language_ontology（语言本体论）

    # 5. 价值论基础（Axiological Foundation）
    axiological_foundation: dict[str, Any] = Field(default_factory=dict)
    # 含：morality_source（道德来源）/ meaning_system（意义系统）/ aesthetic_rules（审美规则）/ value_exchange（价值交换）

    # 6. 生成动力学（Becoming Dynamics）
    becoming_dynamics: dict[str, Any] = Field(default_factory=dict)
    # 含：change_ontology（变化本体论）/ evolution_rules（演化规则）/ entropy_order（熵与秩序）/ transformation_rules（转化规则）

    # 7. 叙事本体论（Narrative Ontology）
    narrative_ontology: dict[str, Any] = Field(default_factory=dict)
    # 含：story_world_relation（故事-世界关系）/ meta_narrative_rules（元叙事规则）/ ending_logic（结局逻辑）

    # 提取元数据
    extracted_at: datetime | None = None
    source_chapter_range: tuple[int, int] | None = None  # 提取来源章节区间（闭区间）
