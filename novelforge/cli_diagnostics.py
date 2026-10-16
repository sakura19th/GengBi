"""CLI 统一失败报告；诊断只保留结构信息，不序列化异常原文或帧局部变量。"""

from __future__ import annotations

import argparse
import errno
import json
import logging
import math
import re
import sys
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class UsageError(ValueError):
    """argparse 错误，交给同一个诊断出口。"""


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


@dataclass
class Failure:
    code: str
    message: str
    hint: str
    exit_code: int = 1
    details: dict[str, Any] = field(default_factory=dict)


class CLIProblem(RuntimeError):
    """业务层显式报告失败；message 必须是程序生成的受控说明。"""

    def __init__(self, failure: Failure, *, use_observed_cause: bool = False):
        super().__init__(failure.message)
        self.failure = failure
        self.use_observed_cause = use_observed_cause


_active: Diagnostics | None = None


def clean(value: str) -> str:
    """附加脱敏；不把它当作任意请求/响应原文可安全输出的保证。"""
    value = re.sub(r"(?i)\b(?:sk-|Bearer\s+)[\w.+/=-]+", "[REDACTED]", value)
    value = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", value)
    value = re.sub(
        r"(?i)(api[_-]?key|authorization|password|token)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        value,
    )
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value)
    if _active:
        for secret in _active.secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value[:1200]


def register_secret(value: str | None) -> None:
    if _active and value:
        _active.secrets.add(value)


def phase(name: str, **identifiers: Any) -> None:
    if _active:
        if _active.phase != name:
            _active.observed = None
        _active.phase = name
        for key, value in identifiers.items():
            if (
                key in {"project", "chapter", "run", "stage", "model", "endpoint"}
                and value
            ):
                _active.context[key] = clean(str(value))


def reset_operation(args: argparse.Namespace) -> None:
    if _active:
        _active.args = args
        _active.observed = None
        _active.events.clear()
        _active.request_fragments.clear()
        _active.context.clear()
        _active.phase = "初始化"


def operation_failed(message: str, internal_reason: str = "") -> CLIProblem:
    """内部字符串仅用于分类，不回显可能夹带远端正文的内容。"""
    reason = internal_reason.lower()
    code, hint = "operation_failed", "查看诊断记录；修复原因后重试已有项目或运行记录。"
    if "json" in reason or "解析" in reason:
        code, hint = (
            "response_json",
            "模型结果不是可解析的数据；可减小 --batch-tokens 或 --lookback 后重试。",
        )
    elif "超时" in reason or "timeout" in reason:
        code, hint = "timeout", "检查服务状态、代理和请求超时；不要反复导入同一文章。"
    elif "保存" in reason or "持久化" in reason:
        code, hint = (
            "persistence_failed",
            "检查数据目录权限、可用空间及文件是否被占用。",
        )
    elif "无 choices" in reason:
        code, hint = (
            "response_schema",
            "服务未返回 choices；检查是否使用 Chat Completions 兼容接口。",
        )
    elif "未配置" in reason or "key 无效" in reason:
        code, hint = (
            "configuration",
            "运行 gengbi doctor 和 gengbi config show 检查对应流程的端点与模型。",
        )
    elif "取消" in reason:
        return CLIProblem(
            Failure("cancelled", "任务已取消。", "已保存的项目和阶段仍可使用。", 130)
        )
    elif "没有有效条目" in reason:
        code, hint = (
            "empty_extraction",
            "模型没有返回有效设定条目；检查输入范围和所用模型。",
        )
    return CLIProblem(
        Failure(code, message, hint), use_observed_cause=code != "empty_extraction"
    )


def require_model_text(
    content: str, finish: str | None, label: str = "模型输出"
) -> None:
    if finish == "length":
        raise CLIProblem(
            Failure(
                "output_truncated",
                f"{label}达到输出 Token 上限，未完整生成。",
                "增加输出上限或减少任务长度；提取可减小 --batch-tokens。",
                details={"finish_reason": "length"},
            )
        )
    if finish in {"content_filter", "safety", "blocked", "refusal"}:
        raise CLIProblem(
            Failure(
                "provider_filtered",
                f"{label}被服务商内容策略终止。",
                "检查服务商允许的内容范围并调整任务；这不是连接故障。",
                details={"finish_reason": finish},
            )
        )
    if not isinstance(content, str) or not content.strip():
        raise CLIProblem(
            Failure(
                "empty_response",
                f"{label}没有返回可用正文。",
                "确认模型支持 Chat Completions 文本输出，检查推理预算和输出上限。",
            )
        )
    if finish != "stop":
        raise CLIProblem(
            Failure(
                "incomplete_response",
                f"{label}没有正常结束。",
                "检查服务商结束标记；可对比 --stream 与 --no-stream，确认响应是否中断。",
                details={
                    "finish_reason": finish
                    if finish in {None, "done", "tool_calls", "function_call"}
                    else "unknown"
                },
            )
        )


def _stack(error: BaseException) -> list[dict[str, Any]]:
    return [
        {"file": Path(f.filename).name, "function": f.name, "line": f.lineno}
        for f in traceback.extract_tb(error.__traceback__)[-16:]
    ]


def capture_request(payload: dict, headers: dict) -> None:
    """只在内存保留脱敏匹配材料，不将请求写入诊断。"""
    if not _active:
        return
    _active.request_fragments.clear()
    for value in headers.values():
        register_secret(str(value))
        if str(value).lower().startswith("bearer "):
            register_secret(str(value)[7:])

    def visit(value):
        if isinstance(value, str):
            normalized = " ".join(value.split())
            if len(normalized) < 12:
                register_secret(normalized)
            else:
                _active.request_fragments.update(
                    normalized[i : i + 12] for i in range(len(normalized) - 11)
                )
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)

    for message in payload.get("messages", []):
        if isinstance(message, dict):
            visit(message.get("content"))
            visit(message.get("tool_calls", []))

    def credentials(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in {
                    "api_key",
                    "apikey",
                    "key",
                    "token",
                    "access_token",
                    "authorization",
                    "password",
                } and isinstance(item, str):
                    register_secret(item)
                else:
                    credentials(item)
        elif isinstance(value, list):
            for item in value:
                credentials(item)

    credentials(payload)


def provider_text(value: str) -> str:
    text = " ".join(value[:8000].split())
    if _active and _active.request_fragments:
        hidden = [False] * len(text)
        for i in range(max(0, len(text) - 11)):
            if text[i : i + 12] in _active.request_fragments:
                hidden[i : i + 12] = [True] * 12
        text = "".join("\x00" if hidden[i] else c for i, c in enumerate(text))
        text = re.sub(r"\x00+", "[REQUEST REDACTED]", text)
    text = re.sub(r"(?i)\b(?:private|secret)[-_][\w-]+", "[REDACTED]", text)
    text = re.sub(
        r"(?i)([?&](?:key|api_key|token|access_token)=)[^&\s]+", r"\1[REDACTED]", text
    )
    return clean(text)[:800]


def provider_details(error: BaseException) -> dict[str, Any]:
    """公开错误信封中的指定字段；未知字段和完整响应体不输出。"""
    result = {}
    body = getattr(error, "body", "")
    try:
        envelope = json.loads(body) if body else {}
    except (ValueError, TypeError):
        envelope = None
    if isinstance(envelope, dict):
        item = envelope.get("error", envelope)
        if "error" not in envelope and not envelope.get("message"):
            item = {
                **envelope,
                "message": envelope.get("detail") or envelope.get("error_description"),
            }
        if isinstance(item, str):
            result["provider_message"] = provider_text(item)
        elif isinstance(item, dict):
            for field in ("message", "code", "type", "param", "status"):
                value = item.get(field)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    result["provider_" + field] = provider_text(str(value))
        for source in (envelope, item):
            if not isinstance(source, dict):
                continue
            for field in ("request_id", "requestId", "trace_id"):
                if isinstance(source.get(field), str):
                    result["request_id"] = provider_text(source[field])
                    break
    elif body:
        # HTML/纯文本网关错误仍有可用原因；去掉脚本、标记和长正文。
        text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", "", body[:8000])
        text = re.sub(r"<[^>]+>", " ", text)
        result["provider_message"] = provider_text(text)
    headers = getattr(error, "response_headers", {})
    for field in ("x-request-id", "request-id", "x-trace-id", "traceparent", "cf-ray"):
        value = next((v for k, v in headers.items() if k.lower() == field), None)
        if value:
            result["request_id"] = provider_text(str(value))
            break
    if not result.get("provider_message"):
        result["provider_message_unavailable"] = (
            "服务未返回可识别的错误说明；请凭请求 ID 向服务商查询。"
        )
    return result


def _api_failure(error: BaseException) -> Failure:
    status = getattr(error, "status", None)
    details = {"http_status": status} if isinstance(status, int) else {}
    details.update(provider_details(error))
    # 原始响应只用于分类，公开字段由 provider_details 脱敏。
    body = str(getattr(error, "body", "") or str(error)).lower()
    category = "api_error"
    hint = "检查 API 根地址、模型 ID 与服务商状态。"
    if any(
        x in body
        for x in (
            "insufficient_quota",
            "insufficient balance",
            "余额不足",
            "quota exceeded",
        )
    ):
        category, hint = "quota_exhausted", "服务商额度或余额不足；检查账户余额和配额。"
    elif any(
        x in body
        for x in ("context_length", "context window", "too many tokens", "上下文长度")
    ):
        category, hint = (
            "context_limit",
            "输入超过服务商上下文限制；减小 --lookback 或提取的 --batch-tokens。",
        )
    elif any(x in body for x in ("max_tokens", "max_completion_tokens")):
        category, hint = (
            "output_limit_parameter",
            "服务商不接受本次输出 Token 参数；检查模型支持的输出上限。",
        )
    elif any(
        x in body for x in ("model_not_found", "model not found", "unknown model")
    ):
        category, hint = (
            "model_not_found",
            "运行 gengbi config models 核对可用模型 ID。",
        )
    elif status == 403:
        hint = "服务商拒绝访问；检查模型权限和账户状态。不能仅凭 403 判断是内容拒答。"
    elif status == 404:
        hint = "核对 API 根地址和模型 ID；接口应为 /v1/chat/completions。"
    elif isinstance(status, int) and status >= 500:
        hint = "服务商或上游服务异常；稍后再试并检查服务状态。"
    return Failure(
        category,
        "服务在 HTTP 200 响应中返回了错误。" if status == 200 else f"API 请求失败（HTTP {status}）。" if status else "API 请求失败。",
        hint,
        details=details,
    )


def describe(error: BaseException, seen: set[int] | None = None) -> Failure:
    seen = set() if seen is None else seen
    if id(error) in seen:
        return Failure(
            "model_failed",
            "模型调用失败，异常因果链不完整。",
            "查看诊断文件中的异常位置。",
        )
    seen.add(id(error))
    if isinstance(error, CLIProblem):
        if (
            error.use_observed_cause
            and _active
            and _active.observed
            and _active.observed is not error
        ):
            failure = describe(_active.observed, seen)
            failure.message = error.failure.message + " " + failure.message
            return failure
        return error.failure
    name = type(error).__name__
    if isinstance(error, (KeyboardInterrupt, EOFError)) or name == "CancelledError":
        return Failure("cancelled", "任务已取消。", "已保存的项目和阶段仍可使用。", 130)
    if isinstance(error, UsageError):
        message = str(error)
        if "unrecognized arguments:" in message:
            message = "存在无法识别的参数；请核对参数名称和所在子命令。"
        else:
            message = re.sub(
                r"invalid (?:choice|[^:]+ value):.*",
                "参数值无效，请查看 --help 中的允许值。",
                message,
            )
        return Failure("usage", clean(message), "运行对应命令的 --help 查看参数。", 2)
    if isinstance(error, ModuleNotFoundError):
        return Failure(
            "dependency",
            "缺少运行依赖。",
            "源码安装 requirements-cli.txt 中的依赖；独立 EXE 请重新安装完整构建。",
            details={"module": clean(error.name or "unknown")},
        )
    if name == "AuthError":
        return Failure(
            "authentication",
            "API 认证失败（HTTP 401）。",
            "检查对应端点的密钥是否有效，不要把密钥粘贴到诊断记录。",
            details={"http_status": 401, **provider_details(error)},
        )
    if name == "RateLimitError":
        retry = getattr(error, "retry_after", None)
        details = {"http_status": 429, **provider_details(error)}
        if isinstance(retry, (int, float)) and math.isfinite(retry):
            details["retry_after_seconds"] = retry
        return Failure(
            "rate_limited",
            "API 请求被限流（HTTP 429）。",
            "降低并发或等待 Retry-After 后重试；检查服务商配额。",
            details=details,
        )
    if name == "APIError":
        return _api_failure(error)
    if name == "ResponseError":
        reasons = {
            "output_truncated": (
                "模型输出达到 Token 上限，响应被截断。",
                "续写、审稿或流程可提高 --max-tokens；提取可减小 --batch-tokens 或 --lookback；不要超过模型输出上限。",
            ),
            "provider_filtered": (
                "服务商以内容过滤标记终止响应。",
                "检查服务商内容范围并调整任务；不要把它当作网络故障。",
            ),
            "provider_refusal": (
                "服务商明确返回了 refusal 拒答字段。",
                "检查服务商允许的任务范围；不自动重试同一请求。",
            ),
            "empty_response": (
                "模型未返回正文内容。",
                "检查模型输出模式、推理预算及输出上限。",
            ),
            "response_json": (
                "服务返回了无法解析的 JSON 数据。",
                "检查接口兼容性及服务商响应；可对比 --no-stream。",
            ),
            "incomplete_response": (
                "模型响应中断或缺少正常结束标记。",
                "检查服务状态及流式协议；可尝试 --no-stream。",
            ),
            "response_tool_calls": (
                "模型返回了工具调用，当前写作命令需要正文文本。",
                "检查端点 extra_payload 中的 tools/tool_choice/functions/function_call，移除强制工具调用设置后重试。",
            ),
            "unsupported_finish_reason": (
                "服务商返回了不支持的响应结束原因。",
                "根据 finish_reason 核对服务商的 Chat Completions 协议；此响应不会保存为完整正文。",
            ),
            "response_schema": (
                "模型响应结构不符合 Chat Completions 协议。",
                "核对 API 根地址和接口类型。",
            ),
        }
        code = getattr(error, "code", "response_schema")
        if code not in reasons:
            code = "response_schema"
        message, hint = reasons[code]
        finish = getattr(error, "finish_reason", None)
        details = {}
        if isinstance(finish, str):
            details["finish_reason"] = provider_text(finish)[:120]
        elif code == "incomplete_response":
            details["finish_reason"] = None
        return Failure(code, message, hint, details=details)
    if isinstance(error, TimeoutError):
        return Failure(
            "timeout",
            "等待模型或网络响应超时。",
            "检查服务商状态和代理；可缩小输入范围或调整请求超时。",
        )
    if "Certificate" in name or "SSLError" in name:
        return Failure(
            "tls",
            "TLS 证书或安全连接校验失败。",
            "检查系统时间、证书和代理配置，不要关闭证书校验。",
        )
    if "Proxy" in name:
        return Failure(
            "proxy", "无法通过配置的代理连接服务。", "检查代理地址、端口和运行状态。"
        )
    if name in {
        "ClientConnectorError",
        "ClientConnectorDNSError",
        "ClientConnectionError",
        "ServerDisconnectedError",
        "ClientOSError",
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
    }:
        return Failure(
            "connection", f"网络连接失败（{name}）。", "检查服务地址、DNS、网络和代理。"
        )
    if name in {"ContentTypeError", "ClientPayloadError"}:
        return Failure(
            "response_protocol",
            f"服务响应格式或传输不完整（{name}）。",
            "确认接口兼容 Chat Completions；可对比 --stream 与 --no-stream。",
        )
    if isinstance(error, json.JSONDecodeError):
        remote = bool(
            _active
            and _active.phase.startswith(("模型", "提取", "审稿", "流程", "整卷"))
        )
        return Failure(
            "response_json" if remote else "input_json",
            "模型返回的 JSON 无法解析。" if remote else "输入 JSON 文件格式错误。",
            "检查结构是否完整；模型输出可因截断、拒答或格式偏离而无法解析。"
            if remote
            else "修正文件中逗号、引号和括号后重试。",
            1 if remote else 2,
            {"line": error.lineno, "column": error.colno},
        )
    if isinstance(error, UnicodeError):
        return Failure(
            "encoding",
            "文件或管道的文本编码不匹配。",
            "将文件保存为 UTF-8；正文读取也可指定 --encoding gb18030。",
            2,
        )
    if name == "ValidationError" and hasattr(error, "errors"):
        fields = [
            {
                "field": clean(".".join(str(p) for p in item["loc"])),
                "type": item["type"],
            }
            for item in error.errors(
                include_input=False, include_context=False, include_url=False
            )[:10]
        ]
        return Failure(
            "validation",
            "数据字段不符合所需格式。",
            "根据 fields 修正字段类型或取值；不需要重新导入已有项目。",
            2,
            {"fields": fields},
        )
    if isinstance(error, FileNotFoundError):
        return Failure(
            "file_missing",
            "文件或目录不存在。",
            "核对路径及当前工作目录。",
            2,
            {"path": clean(str(error.filename))} if error.filename else {},
        )
    if isinstance(error, FileExistsError):
        return Failure(
            "file_exists",
            clean(str(error)),
            "使用新输出路径；确认覆盖时再加 --force。",
            2,
        )
    if isinstance(error, PermissionError):
        return Failure(
            "permission",
            "文件或目录无权限，或正在被其他程序占用。",
            "检查目录写入权限并关闭占用文件的程序。",
            2,
        )
    if isinstance(error, OSError):
        code = "disk_full" if error.errno == errno.ENOSPC else "filesystem"
        return Failure(
            code,
            "磁盘空间不足。"
            if code == "disk_full"
            else f"文件系统操作失败（{name}）。",
            "检查可用空间、路径和文件占用。",
            2,
            {"errno": error.errno},
        )
    if name in {"OperationalError", "DatabaseError", "IntegrityError"}:
        return Failure(
            "database",
            f"本地数据库操作失败（{name}）。",
            "检查数据目录、磁盘空间及其他进程占用；不要删除项目文件来重试。",
        )
    if name in {
        "TemplateSyntaxError",
        "UndefinedError",
        "SecurityError",
        "TemplateRuntimeError",
    }:
        return Failure(
            "template",
            f"预设模板处理失败（{name}）。",
            "检查模板语法及允许使用的变量和函数。",
            2,
        )
    if name == "LLMError":
        cause = error.__cause__
        if cause:
            return describe(cause, seen)
        if _active and _active.observed and _active.observed is not error:
            return describe(_active.observed, seen)
        return Failure(
            "model_failed",
            "模型请求未完整完成。",
            "查看诊断记录中的异常类型与阶段；当前异常未携带可安全公开的详细原因。",
        )
    if isinstance(error, ValueError):
        if _active and _active.phase.startswith(
            ("模型", "提取", "审稿", "流程", "整卷")
        ):
            return Failure(
                "response_validation",
                "模型结果未通过业务数据校验。",
                "检查模型的结构化输出能力；诊断文件可定位校验位置。",
            )
        return Failure(
            "invalid_input",
            clean(str(error)),
            "检查对应命令的 --help，以及项目或资源的 list 结果。",
            2,
        )
    return Failure(
        "internal",
        f"程序发生未预期异常（{name}）。",
        "保留诊断文件用于排查；不要反复付费重试。",
    )


class Diagnostics(logging.Handler):
    def __init__(self, args: argparse.Namespace | None = None):
        super().__init__(logging.WARNING)
        self.args = args
        self.phase = "参数解析"
        self.context: dict[str, str] = {}
        self.secrets: set[str] = set()
        self.request_fragments: set[str] = set()
        self.observed: BaseException | None = None
        self.events: list[dict[str, Any]] = []
        self.previous_level = logging.getLogger("novelforge").level
        self.previous_propagate = logging.getLogger("novelforge").propagate

    def start(self) -> None:
        global _active
        _active = self
        logger = logging.getLogger("novelforge")
        logger.setLevel(logging.WARNING)
        logger.addHandler(self)
        self.previous_propagate = logger.propagate
        logger.propagate = False

    def close(self) -> None:
        global _active
        logger = logging.getLogger("novelforge")
        logger.removeHandler(self)
        logger.setLevel(self.previous_level)
        logger.propagate = self.previous_propagate
        _active = None
        super().close()

    def emit(self, record: logging.LogRecord) -> None:
        values = record.args if isinstance(record.args, tuple) else ()
        error = (
            record.exc_info[1]
            if record.exc_info
            else next((v for v in values if isinstance(v, BaseException)), None)
        )
        event: dict[str, Any] = {
            "logger": record.name,
            "file": Path(record.pathname).name,
            "line": record.lineno,
            "level": record.levelname,
        }
        if error:
            if not isinstance(record.msg, str) or not any(
                word in record.msg for word in ("关闭", "回调", "缓存", "清理")
            ):
                self.observed = error
            event["exception_type"] = type(error).__name__
            event["frames"] = _stack(error)
        if isinstance(record.msg, str) and "批次 %d/%d 第 %d 次" in record.msg:
            numbers = [
                value
                for value in values
                if isinstance(value, int) and not isinstance(value, bool)
            ]
            if len(numbers) >= 3:
                event.update(
                    batch=numbers[0], batch_count=numbers[1], attempt=numbers[2]
                )
                self.context.update(
                    batch=str(numbers[0]),
                    batch_count=str(numbers[1]),
                    attempt=str(numbers[2]),
                )
        self.events.append(event)
        del self.events[:-50]

    def report(self, error: BaseException) -> int:
        failure = describe(error)
        report: dict[str, Any] = {
            "error_id": uuid.uuid4().hex[:12],
            "time": datetime.now(UTC).isoformat(),
            "code": failure.code,
            "message": failure.message,
            "hint": failure.hint,
            "exit_code": failure.exit_code,
            "phase": self.phase,
            "context": self.context,
            "details": failure.details,
            "exception_type": type(error).__name__,
            "frames": _stack(error),
            "events": self.events,
        }
        chain = []
        cause = error.__cause__ or error.__context__
        visited = {id(error)}
        while cause is not None and id(cause) not in visited and len(chain) < 8:
            visited.add(id(cause))
            chain.append(
                {"exception_type": type(cause).__name__, "frames": _stack(cause)}
            )
            cause = cause.__cause__ or cause.__context__
        if chain:
            report["cause_chain"] = chain
        if self.observed:
            report["observed_cause"] = {
                "exception_type": type(self.observed).__name__,
                "frames": _stack(self.observed),
            }
        args = self.args
        project = self.context.get("project")
        run = self.context.get("run")
        if project and getattr(args, "command", None) == "extract":
            report["retry"] = f"gengbi extract {args.kind} --project {project}"
        elif run and getattr(args, "command", None) in {"flows", "volume"}:
            report["retry"] = f"gengbi {args.command} resume {run}"
        if "retry" in report:
            report["retry"] += " （保留原来的配置、范围和输出参数）"
        directory = getattr(args, "diagnostics_dir", None)
        if directory is None:
            config = getattr(args, "config", None)
            directory = (
                Path(config).parent if config else Path.home() / ".novelforge"
            ) / "logs/cli-errors"
        try:
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{report['error_id']}.json"
            # x 防止覆盖已有诊断。记录中不保存 argv、请求/响应或原始异常字符串。
            with path.open("x", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
            report["diagnostic_file"] = str(path.resolve())
        except OSError as write_error:
            report["diagnostic_write_error"] = type(write_error).__name__
        if getattr(args, "error_format", "text") == "json":
            print(
                json.dumps(
                    {"error" if failure.exit_code else "warning": report},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
        else:
            label = "错误" if failure.exit_code else "提示"
            print(f"{label} [{failure.code}]: {failure.message}", file=sys.stderr)
            print(f"阶段: {self.phase}", file=sys.stderr)
            if self.context:
                print(
                    "定位: " + "；".join(f"{k}={v}" for k, v in self.context.items()),
                    file=sys.stderr,
                )
            if failure.details:
                print(
                    "详情: " + json.dumps(failure.details, ensure_ascii=False),
                    file=sys.stderr,
                )
            print(f"建议: {failure.hint}", file=sys.stderr)
            if "retry" in report:
                print(f"重试入口: {report['retry']}", file=sys.stderr)
            if "diagnostic_file" in report:
                print(f"诊断: {report['diagnostic_file']}", file=sys.stderr)
            else:
                print(
                    "诊断文件写入失败，但上面的原始失败类型未被覆盖。", file=sys.stderr
                )
            if getattr(args, "debug", False):
                print(
                    json.dumps(
                        {
                            "exception_type": report["exception_type"],
                            "frames": report["frames"],
                            "events": report["events"],
                        },
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                )
        return failure.exit_code


def report_error(error: BaseException) -> int:
    if _active:
        return _active.report(error)
    failure = describe(error)
    print(
        f"错误 [{failure.code}]: {failure.message}\n建议: {failure.hint}",
        file=sys.stderr,
    )
    return failure.exit_code


@contextmanager
def preserve_failure():
    """finally 中的清理/恢复写盘失败不能替换正在传播的原始错误。"""
    primary = sys.exception()
    try:
        yield
    except Exception as secondary:
        if primary is None:
            raise
        if _active:
            _active.events.append(
                {
                    "secondary_error": type(secondary).__name__,
                    "frames": _stack(secondary),
                }
            )
            _active.context["cleanup_failed"] = "true"
