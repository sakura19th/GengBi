"""调用方可选择严格失败语义；默认保留桌面端的容错行为。"""
from contextvars import ContextVar

strict_cli: ContextVar[bool] = ContextVar("strict_cli", default=False)
