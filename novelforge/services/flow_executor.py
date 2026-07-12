"""流程控制插件阶段执行引擎。

FlowExecutor 按 ``FlowPlugin.stages`` 有序执行每个阶段，通过回调机制
调度 main_window 中注册的 agent handler。支持「挂起-恢复」模式以处理
涉及用户交互的多阶段流程（如重写当前章节的分析→生成两步流程）。

Agent handler 注册::

    executor.register_handler("continuation", handler_func)

Handler 签名::

    handler(stage: FlowStage, params: dict, context: dict) -> Any

Handler 返回值约定：
    - ``"pending"``：等待用户交互（如 AuditDialog 采纳），流程挂起，
      后续由 ``resume(output)`` 恢复
    - ``"cancel"``：用户取消，中断流程
    - 其他：阶段输出，经 ``input_from`` 传入下一阶段

4 种 agent 类型：
    - ``continuation``：ContinuationWorker 流式续写，创建 swipe
    - ``audit``：AuditWorker 低温审计/分析，弹 AuditDialog 供用户审阅
    - ``checkpoint``：暂停点，等待用户确认/编辑/取消
    - ``volume_pipeline``：VolumeOrchestrator 7 阶段卷续写（内部封装）

设计参见 spec.md「流程控制插件系统」一节。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from novelforge.models import FlowPlugin, FlowStage

logger = logging.getLogger(__name__)

# handler 返回值约定
PENDING: str = "pending"
CANCEL: str = "cancel"


class FlowExecutor:
    """流程控制插件阶段执行引擎。

    按 ``FlowPlugin.stages`` 有序执行，每阶段调度到注册的 agent handler。
    多阶段流程通过 ``input_from`` 传递上一阶段输出。

    涉及用户交互的阶段（如 audit）返回 ``"pending"`` 挂起流程，
    用户交互完成后调用 :meth:`resume` 恢复执行。

    Usage::

        executor = FlowExecutor()
        executor.register_handler("continuation", my_continuation_handler)
        executor.register_handler("audit", my_audit_handler)
        executor.execute(plugin, params, context)
        # audit handler 返回 "pending" 后，用户采纳分析结果：
        executor.resume(analysis_text)
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[FlowStage, dict, dict], Any]] = {}
        # 当前执行状态（execute 时设置，resume 时推进）
        self._plugin: FlowPlugin | None = None
        self._params: dict = {}
        self._context: dict = {}
        self._stage_index: int = 0
        self._prev_output: Any = None

    def register_handler(
        self, agent_type: str, handler: Callable[[FlowStage, dict, dict], Any]
    ) -> None:
        """注册 agent handler。

        Args:
            agent_type: agent 类型（continuation/audit/checkpoint/volume_pipeline）
            handler: 处理函数，签名为 ``handler(stage, params, context) -> Any``
        """
        self._handlers[agent_type] = handler

    def execute(self, plugin: FlowPlugin, params: dict, context: dict) -> None:
        """执行插件流程。

        从第一阶段开始按序执行。若某阶段返回 ``"pending"`` 则挂起，
        等待 :meth:`resume` 恢复；返回 ``"cancel"`` 则中断流程。

        Args:
            plugin: 流程插件
            params: 续写参数（来自面板，含 model/temperature 等）
            context: 执行上下文（chapter/entries/project 等，由 main_window 提供）
        """
        self._plugin = plugin
        self._params = params
        self._context = context
        self._stage_index = 0
        self._prev_output = None
        logger.info("开始执行流程插件: %s（%d 阶段）", plugin.id, len(plugin.stages))
        self._execute_current_stage()

    def resume(self, output: Any) -> None:
        """用户交互完成后恢复执行。

        典型场景：audit handler 启动 AuditDialog 后返回 ``"pending"``，
        用户采纳分析结果后调用 :meth:`resume(analysis_text)` 推进下阶段。

        Args:
            output: 上一阶段的输出（如分析文本），将作为下一阶段的 ``_prev_output``
        """
        if self._plugin is None:
            logger.warning("resume 被调用但无活跃流程")
            return
        self._prev_output = output
        self._stage_index += 1
        self._execute_current_stage()

    def cancel(self) -> None:
        """取消当前流程（清理执行状态）。"""
        logger.info("取消流程插件: %s", self._plugin.id if self._plugin else "(无)")
        self._plugin = None
        self._stage_index = 0
        self._prev_output = None

    @property
    def is_active(self) -> bool:
        """是否有活跃的流程正在执行（含挂起状态）。"""
        return self._plugin is not None

    @property
    def current_stage(self) -> FlowStage | None:
        """当前正在执行或挂起的阶段（无活跃流程时返回 None）。"""
        if self._plugin is None or self._stage_index >= len(self._plugin.stages):
            return None
        return self._plugin.stages[self._stage_index]

    def _execute_current_stage(self) -> None:
        """执行当前阶段，根据返回值决定推进/挂起/中断。"""
        if self._plugin is None:
            return
        if self._stage_index >= len(self._plugin.stages):
            logger.info("流程插件执行完成: %s", self._plugin.id)
            self._plugin = None
            return

        stage = self._plugin.stages[self._stage_index]
        handler = self._handlers.get(stage.agent)
        if handler is None:
            logger.error("未注册的 agent 类型: %s（阶段 %s）", stage.agent, stage.id)
            self._plugin = None
            raise ValueError(f"未注册的 agent 类型: {stage.agent}")

        # 合并参数：面板参数 + 阶段 params（阶段覆盖面板）
        stage_params = {**self._params, **stage.params}
        # 上一阶段输出作为本阶段输入
        if stage.input_from and self._prev_output is not None:
            stage_params["_prev_output"] = self._prev_output

        logger.info(
            "执行阶段 %d/%d: %s（agent=%s）",
            self._stage_index + 1,
            len(self._plugin.stages),
            stage.id,
            stage.agent,
        )
        result = handler(stage, stage_params, self._context)

        if result == CANCEL:
            logger.info("用户取消，中断流程: %s", self._plugin.id)
            self._plugin = None
            return

        if result == PENDING:
            logger.info("阶段 %s 挂起等待用户交互", stage.id)
            return

        # 正常完成，推进下一阶段
        self._prev_output = result
        self._stage_index += 1
        self._execute_current_stage()
