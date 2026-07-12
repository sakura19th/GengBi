"""流程控制插件数据模型。

定义 ``FlowStage`` 与 ``FlowPlugin``，用于声明式描述续写流程。

每种流程插件由有序阶段列表组成，每阶段声明 agent 类型、flow_key、参数等。
插件以单个 JSON 文件形式导入导出，不含可执行代码，安全可分享。

设计参见 spec.md「流程控制插件系统」一节。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FlowStage(BaseModel):
    """流程阶段定义。

    一个阶段对应一次 agent 调用（续写/审计/检查点/卷续写），
    FlowExecutor 按 ``FlowPlugin.stages`` 顺序依次执行。

    Attributes:
        id: 阶段唯一标识（插件内唯一，用于 ``input_from`` 引用）
        name: 阶段显示名称（UI 展示用）
        agent: agent 类型，取值：
            - ``continuation``：ContinuationWorker 流式续写，创建 swipe
            - ``audit``：AuditWorker 低温审计/分析，弹 AuditDialog 供用户审阅
            - ``checkpoint``：暂停点，等待用户确认/编辑/取消（无 worker）
            - ``volume_pipeline``：VolumeOrchestrator 7 阶段卷续写（内部封装，向后兼容）
            - ``volume_phase``：VolumeOrchestrator 单阶段执行，emit phase_output
              后挂起，由 main_window resume 推进
        flow_key: 端点/模型/破限选择键（如 single_continuation/rewrite_analysis）
        streaming: 是否流式输出（continuation/audit/volume_pipeline 默认 True）
        created_by: swipe 来源标记，控制 accept 行为
           （如 ``"continuation"``/``"rewrite_current"``/``"volume"``）
        params: 阶段参数（如 ``{"exclude_current": true}``）
        input_from: 上一阶段输出作为本阶段输入的阶段 id
            （空串=用面板参数；非空=取该阶段输出作为本阶段 ``_prev_output``）
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str = ""
    agent: str
    flow_key: str = ""
    streaming: bool = True
    created_by: str = ""
    params: dict = Field(default_factory=dict)
    input_from: str = ""

    @field_validator("agent")
    @classmethod
    def _validate_agent(cls, v: str) -> str:
        """校验 agent 取值合法。"""
        if v not in VALID_AGENT_TYPES:
            valid = "/".join(sorted(VALID_AGENT_TYPES))
            raise ValueError(f"非法 agent: {v!r}，支持取值: {valid}")
        return v


# 合法 agent 类型集合
VALID_AGENT_TYPES: frozenset[str] = frozenset(
    {"continuation", "audit", "checkpoint", "volume_pipeline", "volume_phase"}
)

# 合法 ui_mode 集合
VALID_UI_MODES: frozenset[str] = frozenset({"standard", "volume"})

# 合法 accept_mode 集合
VALID_ACCEPT_MODES: frozenset[str] = frozenset({"promote", "replace", "volume_insert"})


class FlowPlugin(BaseModel):
    """流程控制插件。

    描述一个完整的续写流程，由有序阶段列表组成。
    内置插件（``builtin=True``）不可删除，ID 与原续写模式字符串一致以保兼容。
    用户可导入导出插件 JSON 文件，自行组合既有 agent 或新增 agent 类型。

    Attributes:
        id: 插件唯一标识（内置插件 ID = 原模式字符串：single/volume/rewrite_current）
        name: 插件显示名称
        description: 插件描述
        version: 版本号
        author: 作者
        builtin: 是否内置插件（内置不可删除，导入的强制为 False）
        ui_mode: UI 模式，控制面板显隐：
            - ``standard``：标准续写配置区
            - ``volume``：卷续写面板（隐藏标准配置区）
        accept_mode: 接受续写时的行为：
            - ``promote``：提升为新章节插入当前章之后
            - ``replace``：替换当前章节正文（重写模式）
            - ``volume_insert``：卷续写内部自建章节（accept 不触发）
        stages: 有序阶段列表
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    description: str = ""
    version: str = "1.0"
    author: str = ""
    builtin: bool = False
    ui_mode: str = "standard"
    accept_mode: str = "promote"
    stages: list[FlowStage] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_path_id(cls, v: str) -> str:
        """防御性校验：拒绝含路径字符的 ID，防止导入恶意数据时路径穿越。

        同 :class:`novelforge.models.preset.WritingPreset` 的校验逻辑。
        """
        if v and ("/" in v or "\\" in v or ".." in v or "\x00" in v):
            raise ValueError(f"非法 ID（含路径字符）: {v!r}")
        return v

    @field_validator("ui_mode")
    @classmethod
    def _validate_ui_mode(cls, v: str) -> str:
        """校验 ui_mode 取值合法。"""
        if v not in VALID_UI_MODES:
            valid = "/".join(sorted(VALID_UI_MODES))
            raise ValueError(f"非法 ui_mode: {v!r}，支持取值: {valid}")
        return v

    @field_validator("accept_mode")
    @classmethod
    def _validate_accept_mode(cls, v: str) -> str:
        """校验 accept_mode 取值合法。"""
        if v not in VALID_ACCEPT_MODES:
            valid = "/".join(sorted(VALID_ACCEPT_MODES))
            raise ValueError(f"非法 accept_mode: {v!r}，支持取值: {valid}")
        return v
