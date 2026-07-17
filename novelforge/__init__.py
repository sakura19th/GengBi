"""赓笔 小说续写器包。

基于 PySide6 的桌面小说续写工具，参考 SillyTavern 提示词管线设计。
子模块说明：
- ``ui``：PySide6 界面组件
- ``core``：存储、配置、加密、日志等核心基础设施
- ``services``：业务服务层（续写、提取等）
- ``models``：pydantic 数据模型
- ``utils``：通用工具函数
- ``resources``：默认资源文件（预设、提示词、主题样式表）
"""
from __future__ import annotations

__version__ = "0.2.12"
__all__ = ["__version__"]
