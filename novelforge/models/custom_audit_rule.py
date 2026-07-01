"""自定义设定/审计必查项数据模型。

用户输入文本 → AI 结合世界观底层与上下文结构化为 ``CustomAuditRule``，
固化到 ``Project.custom_audit_rules``，作为审计必查项（单一合并维度
``custom_rules_compliance``，一票否决），并注入续写生成与审计全链路。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CustomAuditRule(BaseModel):
    """自定义设定/审计必查项（用户输入 → AI 结构化）。

    字段说明：
    - ``id``：规则唯一 ID（``nf_rule_`` 前缀）
    - ``title``：AI 结构化的简短标题
    - ``raw_input``：用户原始输入文本
    - ``requirement``：AI 结构化的要求描述（注入续写生成提示词，作为硬约束）
    - ``audit_criteria``：AI 结构化的审计检查向（注入审计模板，逐条可检查）
    - ``severity``：严重程度，``critical``（一票否决）/ ``major``（扣分但不否决）
    - ``created_at``：创建时间
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str = ""
    raw_input: str = ""
    requirement: str = ""
    audit_criteria: str = ""
    severity: str = "critical"
    created_at: datetime = Field(default_factory=datetime.now)
