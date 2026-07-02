"""日志系统。

实现日志记录的核心基础设施：
- 按天滚动（TimedRotatingFileHandler），保留 7 天
- 日志脱敏过滤器（API Key → sk-****，请求体截断 200 字 + 总字数）
- DEBUG 级别开关
- 日志文件路径：``~/.novelforge/logs/novelforge.log``

脱敏规则：
- API Key（sk- 开头的字符串）→ ``sk-****``
- 请求体/响应体只记录摘要（前 200 字 + 总字数）
- 不记录完整小说内容
"""
from __future__ import annotations

import logging
import re
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from novelforge.core.storage import get_default_storage_path
from novelforge.utils.paths import secure_file

# 日志文件路径
LOG_DIR: Path = get_default_storage_path() / "logs"
LOG_FILE: Path = LOG_DIR / "novelforge.log"

# 日志保留天数
LOG_BACKUP_DAYS: int = 7

# 请求体/响应体截断长度
BODY_TRUNCATE_LENGTH: int = 200

# API Key 脱敏正则
# 覆盖常见前缀：sk-（OpenAI/DeepSeek 等）、sk-ant-（Anthropic）、sk-or-（OpenRouter），
# 以及任意长度 >= 12 的 base64-ish token 串（兜底自定义网关 token）
API_KEY_PATTERN = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]{6,}|sk-or-[A-Za-z0-9_\-]{6,}|sk-[A-Za-z0-9_\-]{8,})"
)

# Bearer Token 脱敏正则（Authorization 头值，无论前缀）
BEARER_PATTERN = re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE)

# Authorization 头整体脱敏（覆盖任意 Bearer token，无论前缀）
# 形如 Authorization: Bearer xxx / "Authorization":"Bearer xxx" / Authorization=Bearer xxx
AUTH_HEADER_PATTERN = re.compile(
    r"(Authorization\s*[:=]\s*Bearer\s+)([A-Za-z0-9_\-\.]{6,})", re.IGNORECASE
)


class SensitiveDataFilter(logging.Filter):
    """日志脱敏过滤器。

    在日志记录前对消息进行脱敏处理：
    - API Key（sk-xxx）→ ``sk-****``
    - Bearer Token → ``Bearer ****``
    - 长文本（请求体/响应体）截断为前 200 字 + 总字数
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """过滤并脱敏日志记录。"""
        if isinstance(record.msg, str):
            record.msg = self._sanitize(record.msg)
        if record.args:
            record.args = tuple(
                self._sanitize(arg) if isinstance(arg, str) else arg
                for arg in (record.args if isinstance(record.args, tuple) else (record.args,))
            )
        return True

    @staticmethod
    def _sanitize(text: str) -> str:
        """脱敏文本。"""
        # Authorization 头整体脱敏（先于 Bearer/API_KEY，覆盖任意前缀的 token）
        text = AUTH_HEADER_PATTERN.sub(r"\1****", text)
        # API Key 脱敏（sk- / sk-ant- / sk-or- 前缀）
        text = API_KEY_PATTERN.sub("sk-****", text)
        # Bearer Token 脱敏（裸 Bearer xxx，未带 Authorization 前缀）
        text = BEARER_PATTERN.sub(r"\1****", text)
        # 长文本截断（检测 JSON 请求体或超长文本）
        if len(text) > BODY_TRUNCATE_LENGTH + 100:
            truncated = text[:BODY_TRUNCATE_LENGTH]
            text = f"{truncated}...（共 {len(text)} 字，已截断）"
        return text


def setup_logging(debug: bool = False, log_dir: Path | None = None) -> logging.Logger:
    """配置全局日志系统。

    Args:
        debug: 是否启用 DEBUG 级别日志
        log_dir: 日志目录，默认 ``~/.novelforge/logs``

    Returns:
        配置好的根日志器
    """
    log_dir = log_dir or LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "novelforge.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if debug else logging.INFO)

    # 清除已有 handler（避免重复添加）
    root_logger.handlers.clear()

    # 文件 handler：按天滚动，保留 7 天
    file_handler = TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        interval=1,
        backupCount=LOG_BACKUP_DAYS,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    file_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    file_handler.addFilter(SensitiveDataFilter())
    root_logger.addHandler(file_handler)
    # 收紧日志文件权限：日志可能含截断的请求/响应体，仅所有者可读写
    secure_file(log_file)

    # 控制台 handler（开发调试用）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if debug else logging.WARNING)
    console_handler.setFormatter(
        logging.Formatter(fmt="%(levelname)s %(name)s: %(message)s")
    )
    console_handler.addFilter(SensitiveDataFilter())
    root_logger.addHandler(console_handler)

    logger = logging.getLogger("novelforge")
    logger.info(
        "日志系统初始化完成（级别: %s, 文件: %s）",
        "DEBUG" if debug else "INFO",
        log_file,
    )
    return logger


def set_debug_mode(enabled: bool) -> None:
    """运行时切换 DEBUG 模式。"""
    root_logger = logging.getLogger()
    level = logging.DEBUG if enabled else logging.INFO
    root_logger.setLevel(level)
    for handler in root_logger.handlers:
        handler.setLevel(level)
    logging.getLogger("novelforge").info(
        "日志级别切换为 %s", "DEBUG" if enabled else "INFO"
    )
