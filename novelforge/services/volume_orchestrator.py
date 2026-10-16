"""卷续写 Qt 线程适配器；业务逻辑位于无界面的 volume_engine。"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from novelforge.services.volume_engine import VolumeLogic


class VolumeOrchestrator(VolumeLogic, QThread):
    """保持桌面端线程与 Qt 信号接口，复用无界面的编排逻辑。"""

    phase_started = Signal(str)
    phase_finished = Signal(str, object)
    chapter_started = Signal(int)
    chapter_finished = Signal(int, object)
    chapter_step_started = Signal(int, str)
    chunk_received = Signal(str)
    reasoning_received = Signal(str)
    checkpoint_reached = Signal(str, object)
    finished = Signal(object)
    error = Signal(str)
    auth_error = Signal()
    token_count = Signal(int)
    prompt_debug_requested = Signal(str, str, str, str)
    phase_output = Signal(str, object)
