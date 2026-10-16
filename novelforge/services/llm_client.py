"""LLM 流式调用客户端（OpenAI 兼容 API）。

实现 SSE 流式响应的正确解析：
- 增量 UTF-8 解码器（``codecs.getincrementaldecoder('utf-8')``）避免多字节字符截断
- 正确拼接多行 ``data:`` 字段为完整事件
- API 错误时读取 error body（非 ``raise_for_status``），解析 JSON 错误信息
- 推理内容（``reasoning_content``）单独存储
- 收到 ``[DONE]`` 结束流
- API 429 读取 ``Retry-After`` 头，最多重试 3 次
- API 401 不重试，抛出 ``AuthError`` 供 UI 弹窗跳转设置
"""
from __future__ import annotations

import asyncio
import codecs
import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import aiohttp

logger = logging.getLogger(__name__)

# 流式中断保留的最小字符数
MIN_INTERRUPT_CHARS = 100

# Qwen/DashScope thinking_budget 上限（qwen3.8-flash 为 256K）
QWEN_THINKING_BUDGET_MAX = 262144

def coerce_merge_system_messages(value: Any) -> bool:
    """将端点「合并 system 消息」存储值规范为 bool，默认 False（不合并）。

    兼容旧三档：``always`` → True；``auto`` / ``never`` / 空 / 其它 → False。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"always", "true", "1", "yes", "on", "merge"}
    return False

# reasoning_effort 档位 → thinking_budget 映射（None=不设 budget，走模型默认）
_QWEN_EFFORT_BUDGET_MAP: dict[str, int | None] = {
    "none": None,
    "off": None,
    "low": 4096,
    "minimal": 4096,
    "medium": 16384,
    "high": None,
    "max": None,
    "auto": None,
    "xhigh": None,
}


class LLMError(Exception):
    """LLM API 调用基础异常。"""


class AuthError(LLMError):
    """API 认证失败（401），不重试，需跳转设置。"""

    def __init__(self, message: str, body: str = "", headers=None):
        super().__init__(message)
        self.body = body
        self.response_headers = dict(headers or {})


class ResponseError(LLMError):
    """模型响应的结构化失败，不携带模型正文。"""

    def __init__(self, code: str, finish_reason: str | None = None):
        super().__init__(code)
        self.code = code
        self.finish_reason = finish_reason


class RateLimitError(LLMError):
    """API 限流（429），含 Retry-After 秒数。"""

    def __init__(self, message: str, retry_after: float, body: str = "", headers=None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.body = body
        self.response_headers = dict(headers or {})


class APIError(LLMError):
    """API 返回错误状态码（非 401/429）。"""

    def __init__(self, status: int, message: str, body: str = "", headers=None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.response_headers = dict(headers or {})


@dataclass
class StreamChunk:
    """流式 chunk 数据。

    Attributes:
        content: 正文增量文本
        reasoning_content: 推理内容增量文本（DeepSeek/xAI 等）
        finish_reason: 结束原因（非 None 时表示流结束）
    """

    content: str = ""
    reasoning_content: str = ""
    finish_reason: str | None = None


@dataclass
class StreamResult:
    """流式调用结果。

    Attributes:
        content: 完整正文
        reasoning_content: 完整推理内容
        finish_reason: 结束原因
        status: 完成状态（completed/interrupted/failed）
        interrupt_reason: 中断原因（network_error/user_stopped/api_error）
    """

    content: str = ""
    reasoning_content: str = ""
    finish_reason: str | None = None
    status: str = "completed"
    interrupt_reason: str = ""


def _deep_merge_dict(base: dict, extra: dict) -> None:
    """将 extra deep merge 到 base（原地修改）。

    - extra 中的标量值覆盖 base 同名键
    - extra 中的 dict 值与 base 同名 dict 递归合并
    - extra 中的 list 值直接覆盖 base 同名键
    """
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge_dict(base[k], v)
        else:
            base[k] = v


# CLI 请求采用严格协议检查；桌面调用保留既有部分输出行为。
from novelforge.core.runtime_mode import strict_cli as strict_responses


def retry_delay(value: str | None) -> float:
    """不信任服务端 Retry-After，避免无效数值或无限等待。"""
    try:
        seconds = float(value or "5")
    except (ValueError, TypeError):
        return 5.0
    return min(max(seconds, 0.0), 60.0) if math.isfinite(seconds) else 5.0


def validate_finish(finish: str | None) -> None:
    if finish is not None and not isinstance(finish, str):
        raise ResponseError("response_schema")
    if finish == "length":
        raise ResponseError("output_truncated", finish)
    if finish in {"content_filter", "safety", "blocked", "refusal"}:
        raise ResponseError("provider_filtered", finish)
    if finish in {"tool_calls", "function_call"}:
        raise ResponseError("response_tool_calls", finish)
    if finish != "stop":
        raise ResponseError(
            "unsupported_finish_reason" if finish else "incomplete_response", finish
        )


class LLMClient:
    """LLM 流式调用客户端。

    Usage::

        client = LLMClient(base_url="https://api.openai.com/v1", api_key="sk-xxx")
        async for chunk in client.stream_chat_completion(messages, model="gpt-4o"):
            print(chunk.content, end="")
    """

    # 429 最多重试次数
    MAX_RETRIES = 3

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 300.0,
        reasoning_effort: str | None = None,
        extra_payload: dict | None = None,
        extra_headers: dict | None = None,
        proxy: str | None = None,
        merge_system_messages: bool | str | None = None,
        model_prompts: dict[str, str] | None = None,
    ) -> None:
        """初始化 LLM 客户端。

        Args:
            base_url: API 基础 URL（如 ``https://api.openai.com/v1``）
            api_key: API Key
            timeout: 超时秒数（流式/非流式生成均为 sock_read 分块间超时；
                无 total 上限，避免长生成被总时长误杀；fetch_models 仍用短 total）
            reasoning_effort: 思考强度（OpenAI o 系列/DeepSeek V4 等 OpenAI 兼容网关
                支持，取值 auto/minimal/low/medium/high/max；空串/none/off 表示不发送）
            extra_payload: 自定义请求体字段（deep merge 到 payload，如 zenmux
                的 provider_routing_strategy）
            extra_headers: 自定义 HTTP 头（update 到 headers，可覆盖默认头）
            proxy: HTTP/HTTPS 代理 URL（如 ``http://127.0.0.1:7890`` 或
                ``http://user:pass@host:port``），None/空串表示不使用代理
            merge_system_messages: 是否合并多条 system 消息（默认 False=不合并；
                兼容旧字符串 always/auto/never）
        """
        # 参数校验
        if not (base_url.startswith("http://") or base_url.startswith("https://")):
            raise ValueError(
                f"base_url 必须以 http:// 或 https:// 开头，当前值: {base_url!r}"
            )
        # api_key 为空时仅警告（本地 LLM 如 Ollama 无需 API Key，不阻断初始化）
        if not api_key or not api_key.strip():
            logger.warning("api_key 为空，本地 LLM 服务器可忽略此警告")
        elif base_url.startswith("http://"):
            # 明文 HTTP + 非空 API Key：key 将以明文传输，易被 MITM 截获
            logger.warning(
                "base_url 为 http://，API Key 将以明文传输（易被 MITM 截获），建议改用 https://"
            )

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        # 思考强度：非空且不属于禁用集合时写入 payload
        self.reasoning_effort = reasoning_effort or ""
        # 自定义扩展：payload 字段 deep merge，HTTP 头 update
        self.extra_payload: dict = extra_payload or {}
        self.extra_headers: dict = extra_headers or {}
        from novelforge.services.model_prompts import validate_model_prompts
        self.model_prompts = validate_model_prompts({} if model_prompts is None else model_prompts)
        # HTTP/HTTPS 代理 URL（None 表示不走代理）
        self.proxy = proxy.strip() if proxy and proxy.strip() else None
        # 合并 system：默认不合并，由端点选项显式开启
        self.merge_system_messages = coerce_merge_system_messages(merge_system_messages)
        # 短请求（如 GET /models）：保留 total 总超时
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        # 生成请求（流式 + 非流式）：无 total 限制，sock_read 控制分块/空闲超时
        # （检测死连接，不杀长生成；非流式与流式对齐，避免 prefer_non_stream 时 300s 误杀）
        self.request_timeout = aiohttp.ClientTimeout(
            total=None, sock_connect=30, sock_read=timeout
        )
        # 兼容旧属性名（stream_timeout = request_timeout）
        self.stream_timeout = self.request_timeout
        # 复用的 aiohttp.ClientSession（懒加载，绑定到首次使用的事件循环）
        self._session: aiohttp.ClientSession | None = None

    @staticmethod
    def _is_gemini_model(model: str) -> bool:
        """检测是否为 Gemini 模型（通过模型名判断）。"""
        return "gemini" in model.lower()

    @staticmethod
    def _is_xai_model(model: str) -> bool:
        """检测是否为 xAI Grok 模型（通过模型名判断）。"""
        return "grok" in model.lower()

    @staticmethod
    def _is_sampling_deprecated_claude_model(model: str) -> bool:
        """检测 Claude 是否已弃用 temperature/top_p/top_k。

        Fable 5 / Mythos / Opus 4.7+ 发送采样参数会 400；
        Claude 3.x / Opus 4.6 及更早仍支持，不可按 ``claude`` 全量匹配。
        """
        if not model:
            return False
        m = model.lower().replace("_", "-")
        if "fable" in m or "mythos" in m:
            return True
        # opus-4-7 / opus-4.7 / opus-4-8 / opus-4.8 / opus-5（避免误伤 opus-4-6）
        if "opus-4-7" in m or "opus-4.7" in m:
            return True
        if "opus-4-8" in m or "opus-4.8" in m:
            return True
        if "opus-5" in m:
            return True
        return False

    def _resolve_reasoning_effort_for_payload(self, model: str) -> str | None:
        """解析适用于当前模型的 reasoning_effort 值。

        Gemini 模型的 thinking_level 仅支持 low/medium/high，
        其他值（auto/minimal/max）需映射或不发送。

        Returns:
            映射后的值，或 None 表示不写入 payload
        """
        if not self.reasoning_effort or self.reasoning_effort.lower() in {"", "none", "off"}:
            return None

        effort = self.reasoning_effort.lower()

        if self._is_gemini_model(model):
            # Gemini thinking_level 仅支持 low/medium/high
            gemini_map: dict[str, str | None] = {
                "low": "low",
                "medium": "medium",
                "high": "high",
                "minimal": "low",   # 降级到 low
                "max": "high",      # 降级到 high
                "auto": None,       # 不发送，让 Gemini 用默认
            }
            return gemini_map.get(effort)

        return effort

    @staticmethod
    def _filter_unsupported_params(payload: dict, model: str) -> None:
        """按模型类型删除不支持的参数（原地修改）。

        - xAI Grok 全系列不支持 presence_penalty / frequency_penalty；
          除 grok-3-mini 外不支持 reasoning_effort。
        - Gemini 多数型号不支持 presence_penalty / frequency_penalty
         （含 0.0 时部分网关仍 400 INVALID_ARGUMENT）。
        - Claude Fable/Mythos/Opus 4.7+ 不支持 temperature / top_p / top_k
         （发送会 400 ``top_p is deprecated``）；随后仍走 0.0 penalty 省略。
        - 其余模型：penalty 为 0.0 时不发送（与默认行为等价，更兼容）。
        """
        if not model:
            return
        model_lower = model.lower()
        if LLMClient._is_xai_model(model_lower):
            # 所有 Grok 模型不支持 presence/frequency_penalty
            payload.pop("presence_penalty", None)
            payload.pop("frequency_penalty", None)
            # 非 grok-3-mini 不支持 reasoning_effort
            if "grok-3-mini" not in model_lower:
                payload.pop("reasoning_effort", None)
            return
        if LLMClient._is_gemini_model(model_lower):
            # Gemini 2.5/3.x 等不支持 penalty，发送会 400 INVALID_ARGUMENT
            payload.pop("presence_penalty", None)
            payload.pop("frequency_penalty", None)
            return
        # Claude 新旗舰：去掉已弃用采样参数后继续走下方 0.0 penalty 省略
        if LLMClient._is_sampling_deprecated_claude_model(model):
            payload.pop("temperature", None)
            payload.pop("top_p", None)
            payload.pop("top_k", None)
        # 其他模型：0.0 为 no-op，省略可减少部分网关对未知/未启用字段的挑剔
        if payload.get("presence_penalty") == 0.0:
            payload.pop("presence_penalty", None)
        if payload.get("frequency_penalty") == 0.0:
            payload.pop("frequency_penalty", None)

    @staticmethod
    def _normalize_model_key(model: str) -> str:
        """归一化模型名用于 Qwen 族检测（去掉 . _ - / 等分隔符）。"""
        return (
            model.lower()
            .replace(".", "")
            .replace("_", "")
            .replace("-", "")
            .replace("/", "")
        )

    @staticmethod
    def _is_qwen_model(model: str) -> bool:
        """检测是否为 Qwen/QwQ 模型。"""
        if not model:
            return False
        key = LLMClient._normalize_model_key(model)
        return "qwen" in key or key.startswith("qwq")

    @staticmethod
    def _is_qwen38_model(model: str) -> bool:
        """检测是否为 Qwen3.8 模型（原始模型名，避免 Qwen3-8B 误伤）。

        使用小写原始名匹配 ``qwen3.8`` / ``qwen-3.8``，不用去分隔符归一化。
        """
        if not model:
            return False
        m = model.lower()
        return "qwen3.8" in m or "qwen-3.8" in m

    @staticmethod
    def _is_qwen38_flash_model(model: str) -> bool:
        """检测是否为 qwen3.8-flash 族。"""
        return LLMClient._is_qwen38_model(model) and "flash" in model.lower()

    @staticmethod
    def _is_qwen38_max_model(model: str) -> bool:
        """检测是否为 qwen3.8-max 族（支持 reasoning_effort 档位）。"""
        key = LLMClient._normalize_model_key(model)
        return "qwen38max" in key

    @staticmethod
    def _map_qwen38_reasoning_effort(effort: str) -> str | None:
        """将端点档位映射为 Qwen3.8 模板支持的 xhigh/medium/low。"""
        if not effort:
            return None
        e = effort.lower()
        if e in {"auto"}:
            return None
        if e in {"xhigh", "high", "max"}:
            return "xhigh"
        if e == "medium":
            return "medium"
        if e in {"low", "minimal"}:
            return "low"
        return None

    @staticmethod
    def _set_qwen38_disable_thinking(payload: dict) -> None:
        """关闭 Qwen3.8 思考：Qwen Cloud 顶层 + vLLM/SGLang chat_template_kwargs。"""
        payload.pop("reasoning_effort", None)
        payload.pop("thinking_budget", None)
        payload["enable_thinking"] = False
        extra_body = payload.get("extra_body")
        if isinstance(extra_body, dict):
            kwargs = extra_body.setdefault("chat_template_kwargs", {})
            if isinstance(kwargs, dict):
                kwargs["enable_thinking"] = False
            return
        existing = payload.get("chat_template_kwargs")
        if isinstance(existing, dict):
            existing["enable_thinking"] = False
        else:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

    @staticmethod
    def _effort_to_thinking_budget(effort: str) -> int | None:
        """将 reasoning_effort 档位映射为 thinking_budget（None=不设 budget）。"""
        return _QWEN_EFFORT_BUDGET_MAP.get(effort.lower())

    @staticmethod
    def _coerce_thinking_budget(value: Any) -> int | None:
        """将 thinking_budget 原始值规范为 1..QWEN_THINKING_BUDGET_MAX 或 None。

        非数字字符串（如档位名 high）按档位映射；<=0 返回 None（调用方处理 disable）。
        """
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            lower = stripped.lower()
            if lower in {"none", "off", "false"}:
                return None
            if lower in _QWEN_EFFORT_BUDGET_MAP:
                return LLMClient._effort_to_thinking_budget(lower)
            try:
                num = int(stripped)
            except ValueError:
                return None
        elif isinstance(value, float):
            if value != int(value):
                return None
            num = int(value)
        elif isinstance(value, int):
            num = value
        else:
            return None

        if num <= 0:
            return None
        if num > QWEN_THINKING_BUDGET_MAX:
            return QWEN_THINKING_BUDGET_MAX
        return num

    @staticmethod
    def _sanitize_thinking_budget_field(container: dict) -> bool:
        """清洗 container 中的 thinking_budget，返回是否应关闭思考（显式 0/none/off）。

        原地修改 container。
        """
        if "thinking_budget" not in container:
            return False
        raw = container["thinking_budget"]
        if raw == 0 or (isinstance(raw, str) and raw.strip() in {"0", "0.0"}):
            container.pop("thinking_budget", None)
            return True
        if isinstance(raw, str) and raw.strip().lower() in {"none", "off", "false"}:
            container.pop("thinking_budget", None)
            return True
        budget = LLMClient._coerce_thinking_budget(raw)
        if budget is None:
            container.pop("thinking_budget", None)
        else:
            container["thinking_budget"] = budget
        return False

    def _sanitize_qwen38_thinking_params(
        self,
        payload: dict,
        model: str,
        *,
        disable_from_payload: bool,
        disable_from_extra: bool,
        has_budget: bool,
    ) -> None:
        """Qwen3.8 特化思考参数清洗（27B/max/flash）。"""
        effort_raw = payload.pop("reasoning_effort", None) or self.reasoning_effort or ""
        effort = effort_raw.strip().lower() if isinstance(effort_raw, str) else ""

        if disable_from_payload or disable_from_extra or effort in {"none", "off"}:
            self._set_qwen38_disable_thinking(payload)
            return

        # auto / 空档：不发思考参数，走模型默认（禁止把 auto 映射成 xhigh）
        if not effort or effort == "auto":
            return

        if self._is_qwen38_flash_model(model):
            if has_budget:
                return
            budget = self._effort_to_thinking_budget(effort)
            if budget is not None:
                payload["thinking_budget"] = budget
            return

        # Qwen3.8 非 flash（27B、max 等）：映射 reasoning_effort，不用 thinking_budget
        if has_budget:
            return
        mapped = self._map_qwen38_reasoning_effort(effort)
        if mapped:
            payload["reasoning_effort"] = mapped

    def _sanitize_qwen_thinking_params(self, payload: dict, model: str) -> None:
        """按 Qwen 模型族清洗思考参数（必须在 extra_payload merge 之后调用）。

        - Qwen3.8：flash 走 thinking_budget；27B/max 映射 reasoning_effort；
          none/off 写 enable_thinking + chat_template_kwargs
        - 非 Qwen3.8 的 qwen3.8-max（归一化 key）：保留原 max 族逻辑
        - 其余 Qwen：不发 reasoning_effort，档位转 thinking_budget 或 enable_thinking
        """
        if not self._is_qwen_model(model):
            return

        extra_body = payload.get("extra_body")
        extra_body_dict = extra_body if isinstance(extra_body, dict) else None

        disable_from_payload = self._sanitize_thinking_budget_field(payload)
        disable_from_extra = False
        if extra_body_dict is not None:
            disable_from_extra = self._sanitize_thinking_budget_field(extra_body_dict)

        has_budget = (
            "thinking_budget" in payload
            or (extra_body_dict is not None and "thinking_budget" in extra_body_dict)
        )

        if self._is_qwen38_model(model):
            self._sanitize_qwen38_thinking_params(
                payload,
                model,
                disable_from_payload=disable_from_payload,
                disable_from_extra=disable_from_extra,
                has_budget=has_budget,
            )
            return

        if self._is_qwen38_max_model(model):
            # max 族：保留 reasoning_effort；budget 与 effort 同时存在时 budget 优先
            if has_budget:
                payload.pop("reasoning_effort", None)
            elif disable_from_payload or disable_from_extra:
                payload["enable_thinking"] = False
            return

        # flash 及其余 Qwen：移除 reasoning_effort，按档位转换
        effort_raw = payload.pop("reasoning_effort", None) or self.reasoning_effort or ""
        effort = effort_raw.strip().lower() if isinstance(effort_raw, str) else ""

        if disable_from_payload or disable_from_extra:
            payload["enable_thinking"] = False
            return

        if has_budget:
            # 用户 extra_payload 已提供合法 budget，不再注入档位
            return

        if effort in {"none", "off"}:
            payload["enable_thinking"] = False
            return

        if not effort:
            # 端点未配置（空串）：不注入任何思考参数，走模型默认（flash 默认开启思考）
            return

        budget = self._effort_to_thinking_budget(effort)
        if budget is not None:
            payload["thinking_budget"] = budget

    def _apply_payload_extensions(self, payload: dict, model: str) -> None:
        """注入思考强度、过滤不支持参数、合并 extra_payload、清洗 Qwen 思考参数。"""
        effort = self._resolve_reasoning_effort_for_payload(model)
        if effort:
            payload["reasoning_effort"] = effort
        self._filter_unsupported_params(payload, model)
        if self.extra_payload:
            _deep_merge_dict(payload, self.extra_payload)
        self._sanitize_qwen_thinking_params(payload, model)
        # 以扩展合并后的实际模型为准，避免 extra_payload 覆盖消息或模型时漏加/错加。
        prompt = self.model_prompts.get(payload.get("model"), "")
        if prompt:
            messages = payload.get("messages")
            if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
                raise ValueError("启用模型附加提示词时，messages 必须是消息对象列表")
            messages = [dict(message) for message in messages]
            # 放在最后一条原 system 消息之后，保持原提示词和用户正文不变。
            position = max((i + 1 for i, m in enumerate(messages) if m.get("role") == "system"), default=0)
            messages.insert(position, {"role": "system", "content": prompt})
            payload["messages"] = self._prepare_messages_for_model(messages, payload["model"])

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取复用的 aiohttp.ClientSession（懒加载）。

        首次调用时创建 session 并绑定到当前事件循环，后续复用以利用
        TCP 连接池与 DNS 缓存。headers/timeout 由各请求自行传入，
        避免不同调用（流式/非流式/GET）的配置互相干扰。
        """
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """关闭复用的 ClientSession。

        当客户端不再使用时应调用此方法以释放底层 TCP 连接与资源。
        通常在应用退出或客户端替换时调用。
        """
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def stream_chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.8,
        max_tokens: int | None = None,
        top_p: float = 1.0,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
        stop_event: asyncio.Event | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """流式调用 chat/completions 接口。

        使用增量 UTF-8 解码器解析响应流，正确处理多字节字符截断。
        正确拼接多行 ``data:`` 字段。

        Args:
            messages: 消息列表
            model: 模型名
            temperature: 温度
            max_tokens: 最大生成 token 数
            top_p: top_p 采样
            frequency_penalty: 频率惩罚
            presence_penalty: 存在惩罚
            stop_event: 停止事件（设置时中断流）

        Yields:
            StreamChunk 增量数据
        """
        url = f"{self.base_url}/chat/completions"
        messages = self._prepare_messages_for_model(messages, model)
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "top_p": top_p,
            "frequency_penalty": frequency_penalty,
            "presence_penalty": presence_penalty,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        self._apply_payload_extensions(payload, model)

        headers = {
            **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        # 自定义 HTTP 头合并（可覆盖默认头）
        if self.extra_headers:
            headers.update(self.extra_headers)

        if strict_responses.get():
            from novelforge.cli_diagnostics import capture_request
            capture_request(payload, headers)
        retry_count = 0
        last_error: Exception | None = None

        while retry_count <= self.MAX_RETRIES:
            emitted = False
            try:
                session = await self._get_session()
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.request_timeout,
                    proxy=self.proxy or None,
                ) as response:
                    # 处理错误状态码（不调用 raise_for_status）
                    if response.status == 401:
                        body = await response.text()
                        logger.error("API 认证失败 (401): %s", body[:200])
                        raise AuthError("API Key 无效，请检查设置", body)

                    if response.status == 429:
                        body = await response.text()
                        if any(word in body.lower() for word in ("insufficient_quota", "insufficient balance", "余额不足")):
                            raise APIError(429, "quota exhausted", body)
                        retry_after = retry_delay(response.headers.get("Retry-After"))
                        if retry_count < self.MAX_RETRIES:
                            logger.warning(
                                "API 限流 (429)，等待 %.1f 秒后重试 (%d/%d)",
                                retry_after,
                                retry_count + 1,
                                self.MAX_RETRIES,
                            )
                            await asyncio.sleep(retry_after)
                            retry_count += 1
                            continue
                        raise RateLimitError(
                            f"API 限流，已重试 {self.MAX_RETRIES} 次",
                            retry_after, body,
                        )

                    if response.status >= 400:
                        body = await response.text()
                        error_msg = self._parse_error_body(body)
                        logger.error(
                            "API 错误 (%d): %s", response.status, error_msg
                        )
                        raise APIError(response.status, error_msg, body)

                    # 流式解析 SSE
                    finished = False
                    async for chunk in self._parse_sse_stream(response, stop_event):
                        if strict_responses.get() and chunk.finish_reason:
                            validate_finish(chunk.finish_reason)
                            if not emitted and not chunk.content:
                                raise ResponseError("empty_response")
                            finished = True
                        emitted = emitted or bool(chunk.content)
                        yield chunk
                        if finished:
                            return
                    if strict_responses.get() and not finished:
                        raise ResponseError("incomplete_response")
                    return

            except (AuthError, RateLimitError, APIError) as error:
                error.response_headers = dict(response.headers)
                raise
            except asyncio.CancelledError:
                logger.info("流式调用被取消")
                raise
            except aiohttp.ClientError as e:
                if strict_responses.get() and emitted:
                    raise ResponseError("incomplete_response") from e
                last_error = e
                logger.warning("网络错误: %s，重试 %d/%d", e, retry_count + 1, self.MAX_RETRIES)
                if retry_count < self.MAX_RETRIES:
                    await asyncio.sleep(1.0 * (retry_count + 1))
                    retry_count += 1
                    continue
                raise LLMError(f"网络错误: {e}") from e

        if last_error:
            raise LLMError(f"请求失败: {last_error}")

    async def _parse_sse_stream(
        self,
        response: aiohttp.ClientResponse,
        stop_event: asyncio.Event | None,
    ) -> AsyncIterator[StreamChunk]:
        """解析 SSE 流。

        使用增量 UTF-8 解码器避免多字节字符截断，
        按 ``\\n\\n`` 分割事件，提取 ``data:`` 行并拼接多行字段。
        """
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buffer = ""

        async for raw_bytes in response.content.iter_any():
            # 检查停止信号
            if stop_event and stop_event.is_set():
                logger.info("用户请求停止流式输出")
                return

            # 增量解码
            text = decoder.decode(raw_bytes)
            if not text:
                continue

            buffer += text

            # 按空行分割事件（SSE 事件以 \n\n 分隔）
            while "\n\n" in buffer:
                event_str, buffer = buffer.split("\n\n", 1)
                chunk = self._parse_sse_event(event_str)
                if chunk is not None:
                    yield chunk

        # 处理缓冲区剩余数据
        if buffer.strip():
            chunk = self._parse_sse_event(buffer)
            if chunk is not None:
                yield chunk

    def _parse_sse_event(self, event_str: str) -> StreamChunk | None:
        """解析单个 SSE 事件。

        拼接多行 ``data:`` 字段为完整 JSON，解析后提取内容。
        收到 ``[DONE]`` 时返回带 finish_reason 的 chunk。
        """
        data_lines: list[str] = []

        for line in event_str.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                # 提取 data: 后的内容（去掉可选空格）
                data_content = line[5:]
                if data_content.startswith(" "):
                    data_content = data_content[1:]
                data_lines.append(data_content)
            # 忽略 event:, id:, comment 等其他字段

        if not data_lines:
            return None

        # 拼接多行 data 为完整字符串
        full_data = "\n".join(data_lines).strip()

        if full_data == "[DONE]":
            return StreamChunk(finish_reason="done")

        # 解析 JSON
        try:
            data = json.loads(full_data)
        except json.JSONDecodeError as e:
            logger.warning("SSE 数据 JSON 解析失败: %s, data=%s", e, full_data[:100])
            if strict_responses.get():
                raise ResponseError("response_json") from e
            return None

        if strict_responses.get():
            if not isinstance(data, dict):
                raise ResponseError("response_schema")
            if data.get("error"):
                raise APIError(200, "SSE error event", json.dumps(data["error"]))
            if not isinstance(data.get("choices", []), list):
                raise ResponseError("response_schema")
        # 提取 choices[0].delta 内容
        choices = data.get("choices", [])
        if not choices:
            return None

        if strict_responses.get() and not isinstance(choices[0], dict):
            raise ResponseError("response_schema")
        choice0 = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice0.get("delta", {}) or {}
        if not isinstance(delta, dict):
            if strict_responses.get():
                raise ResponseError("response_schema")
            delta = {}
        content = delta.get("content", "") or ""
        if strict_responses.get():
            if delta.get("refusal"):
                raise ResponseError("provider_refusal")
            if not isinstance(content, str):
                raise ResponseError("response_schema")
        # 兼容 DeepSeek reasoning_content / OpenRouter reasoning / 部分网关 thinking
        reasoning_content = LLMClient._extract_reasoning_text(delta)
        if not reasoning_content:
            # 少数网关把推理挂在 choice 顶层而非 delta
            reasoning_content = LLMClient._extract_reasoning_text(choice0)
        finish_reason = choice0.get("finish_reason")

        if not content and not reasoning_content and not finish_reason:
            return None

        return StreamChunk(
            content=content,
            reasoning_content=reasoning_content,
            finish_reason=finish_reason,
        )

    @staticmethod
    def _extract_reasoning_text(payload: dict | None) -> str:
        """从 delta/message 字典提取模型自带思维链文本。

        兼容多种 OpenAI 兼容网关字段：
        - ``reasoning_content``（DeepSeek / 多数中转）
        - ``reasoning``（OpenRouter / 部分 o 系列兼容）
        - ``thinking`` / ``reasoning_text``（少数网关）
        - 嵌套 dict（如 ``{"content": "..."}`` / ``{"text": "..."}``）
        """
        if not isinstance(payload, dict):
            return ""
        for key in ("reasoning_content", "reasoning", "thinking", "reasoning_text"):
            value = payload.get(key)
            if value is None or value is False:
                continue
            if isinstance(value, str):
                if value:
                    return value
                continue
            if isinstance(value, dict):
                nested = (
                    value.get("content")
                    or value.get("text")
                    or value.get("reasoning")
                    or value.get("thinking")
                    or ""
                )
                if isinstance(nested, str) and nested:
                    return nested
                continue
            if isinstance(value, list):
                parts: list[str] = []
                for item in value:
                    if isinstance(item, str) and item:
                        parts.append(item)
                    elif isinstance(item, dict):
                        piece = (
                            item.get("content")
                            or item.get("text")
                            or item.get("thinking")
                            or ""
                        )
                        if isinstance(piece, str) and piece:
                            parts.append(piece)
                if parts:
                    return "".join(parts)
        return ""

    @staticmethod
    def extract_message_reasoning(message: dict | None) -> str:
        """从非流式 ``message`` 对象提取推理文本（字段兼容同流式）。"""
        if not isinstance(message, dict):
            return ""
        return LLMClient._extract_reasoning_text(message)

    @staticmethod
    def _parse_error_body(body: str) -> str:
        """解析 API 错误响应体，提取错误消息。"""
        try:
            data = json.loads(body)
            # OpenAI 格式: {"error": {"message": "..."}}
            if not isinstance(data, dict):
                return "API 返回非对象错误响应"
            if "error" in data:
                err = data["error"]
                if isinstance(err, dict):
                    return err.get("message", str(err))
                return str(err)
            return data.get("message", body[:200])
        except (json.JSONDecodeError, TypeError):
            return body[:200] if body else "未知错误"

    @staticmethod
    def _ensure_user_message(messages: list[dict]) -> list[dict]:
        """确保 messages 至少含一条 user 消息（Gemini 兼容网关兜底）。

        Gemini 兼容网关（如 one-api/new-api 代理）将 system 消息提取到
        systemInstruction，user/assistant 消息放入 contents。若 messages
        全为 system 角色，contents 为空数组，Gemini API 返回 500 错误
        "contents are required"。

        本方法检测此情况，将最后一条 system 消息的角色改为 user（最后一条
        通常是实际任务指令，前面的 system 消息保留为角色设定/破限）。使用
        副本修改，不影响调用方的原始列表。

        Args:
            messages: 原始消息列表

        Returns:
            处理后的消息列表（若已含 user 消息则原样返回）
        """
        if not messages:
            return messages
        if any(m.get("role") != "system" for m in messages):
            return messages
        # 全为 system：将最后一条改为 user（任务指令转为请求角色）
        result = [dict(m) for m in messages]
        result[-1]["role"] = "user"
        return result

    @staticmethod
    def _merge_system_messages(messages: list[dict]) -> list[dict]:
        """合并多条 system 为一条并置于首位（Qwen3.8 chat template 要求）。

        仅当存在多条 system，或唯一 system 不在 index 0 时才合并。
        使用副本，不修改调用方原始列表。
        """
        if not messages:
            return messages
        system_indices = [
            i for i, m in enumerate(messages) if m.get("role") == "system"
        ]
        if not system_indices:
            return messages
        if len(system_indices) == 1 and system_indices[0] == 0:
            return messages

        parts: list[str] = []
        for m in messages:
            if m.get("role") == "system":
                content = m.get("content", "")
                if content:
                    parts.append(content)
        non_system = [dict(m) for m in messages if m.get("role") != "system"]
        if not parts:
            return non_system
        merged = {"role": "system", "content": "\n\n".join(parts)}
        return [merged] + non_system

    def _prepare_messages_for_model(self, messages: list[dict], _model: str) -> list[dict]:
        """发送前归一化 messages：Gemini 兜底 + 按端点选项合并 system。

        ``_model`` 保留与调用方签名一致；是否合并只看 ``merge_system_messages``。
        """
        messages = self._ensure_user_message(messages)
        if self.merge_system_messages:
            messages = self._merge_system_messages(messages)
        return messages

    async def chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.2,
        max_tokens: int | None = 2000,
        top_p: float = 1.0,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
        stop_event: asyncio.Event | None = None,
    ) -> dict:
        """非流式调用 chat/completions，返回完整响应 dict。

        用于上下文提取、prefer_non_stream 续写等场景。
        使用 ``stream: false`` 直接获取完整响应，解析 ``choices[0].message.content``。
        超时与流式一致：无 total 上限，仅 sock_read 检测死连接。

        Args:
            messages: 消息列表
            model: 模型名
            temperature: 温度（默认 0.2，低温保证稳定输出）
            max_tokens: 最大生成 token 数（None 时不写入 payload）
            top_p: top_p 采样
            frequency_penalty: 频率惩罚（Gemini/Grok 等会过滤）
            presence_penalty: 存在惩罚（Gemini/Grok 等会过滤）
            stop_event: 停止事件（设置时中断请求）

        Returns:
            完整响应字典，含 ``choices``、``usage`` 等字段

        Raises:
            AuthError: API 认证失败（401）
            RateLimitError: API 限流（429）
            APIError: API 返回错误状态码
            LLMError: 网络或其他错误
        """
        url = f"{self.base_url}/chat/completions"
        messages = self._prepare_messages_for_model(messages, model)
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
            "top_p": top_p,
            "frequency_penalty": frequency_penalty,
            "presence_penalty": presence_penalty,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        self._apply_payload_extensions(payload, model)

        headers = {
            **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # 自定义 HTTP 头合并（可覆盖默认头）
        if self.extra_headers:
            headers.update(self.extra_headers)

        if strict_responses.get():
            from novelforge.cli_diagnostics import capture_request
            capture_request(payload, headers)
        retry_count = 0
        last_error: Exception | None = None

        while retry_count <= self.MAX_RETRIES:
            # 检查停止信号
            if stop_event and stop_event.is_set():
                logger.info("非流式调用被取消")
                raise asyncio.CancelledError("用户取消请求")

            try:
                session = await self._get_session()
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.request_timeout,
                    proxy=self.proxy or None,
                ) as response:
                    # 处理错误状态码
                    if response.status == 401:
                        body = await response.text()
                        logger.error("API 认证失败 (401): %s", body[:200])
                        raise AuthError("API Key 无效，请检查设置", body)

                    if response.status == 429:
                        body = await response.text()
                        if any(word in body.lower() for word in ("insufficient_quota", "insufficient balance", "余额不足")):
                            raise APIError(429, "quota exhausted", body)
                        retry_after = retry_delay(response.headers.get("Retry-After"))
                        if retry_count < self.MAX_RETRIES:
                            logger.warning(
                                "API 限流 (429)，等待 %.1f 秒后重试 (%d/%d)",
                                retry_after,
                                retry_count + 1,
                                self.MAX_RETRIES,
                            )
                            await asyncio.sleep(retry_after)
                            retry_count += 1
                            continue
                        raise RateLimitError(
                            f"API 限流，已重试 {self.MAX_RETRIES} 次",
                            retry_after, body,
                        )

                    if response.status >= 400:
                        body = await response.text()
                        error_msg = self._parse_error_body(body)
                        logger.error(
                            "API 错误 (%d): %s", response.status, error_msg
                        )
                        raise APIError(response.status, error_msg, body)

                    data = await response.json()
                    if strict_responses.get():
                        if not isinstance(data, dict):
                            raise ResponseError("response_schema")
                        if data.get("error"):
                            raise APIError(response.status, "API error", json.dumps(data["error"]))
                        choices = data.get("choices")
                        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                            raise ResponseError("response_schema")
                        message = choices[0].get("message")
                        if not isinstance(message, dict):
                            raise ResponseError("response_schema")
                        if message.get("refusal"):
                            raise ResponseError("provider_refusal")
                        validate_finish(choices[0].get("finish_reason"))
                        if not isinstance(message.get("content"), str):
                            raise ResponseError("empty_response" if message.get("content") is None else "response_schema")
                        if not message["content"].strip():
                            raise ResponseError("empty_response")
                    logger.debug(
                        "非流式调用成功: model=%s, tokens=%s",
                        model,
                        data.get("usage", {}),
                    )
                    return data

            except (AuthError, RateLimitError, APIError) as error:
                error.response_headers = dict(response.headers)
                raise
            except asyncio.CancelledError:
                logger.info("非流式调用被取消")
                raise
            except aiohttp.ClientError as e:
                last_error = e
                logger.warning(
                    "网络错误: %s，重试 %d/%d", e, retry_count + 1, self.MAX_RETRIES
                )
                if retry_count < self.MAX_RETRIES:
                    await asyncio.sleep(1.0 * (retry_count + 1))
                    retry_count += 1
                    continue
                raise LLMError(f"网络错误: {e}") from e

        if last_error:
            raise LLMError(f"请求失败: {last_error}")

        raise LLMError("请求失败：未知原因")

    async def complete_text(
        self,
        messages: list[dict],
        model: str,
        *,
        stream: bool = True,
        temperature: float = 0.2,
        max_tokens: int | None = 2000,
        top_p: float = 1.0,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
        stop_event: asyncio.Event | None = None,
        on_chunk: Any = None,
    ) -> tuple[str, str | None]:
        """统一文本生成：流式或非流式，返回 (content, finish_reason)。

        流式时逐 chunk 可选回调 ``on_chunk(str)``；非流式完成后若提供
        on_chunk 则一次性推送全文。超时策略与 stream/chat 路径一致。

        Args:
            messages: 消息列表
            model: 模型名
            stream: True=SSE 流式，False=一次返回
            temperature: 温度
            max_tokens: 最大生成 token
            top_p: top_p
            frequency_penalty: 频率惩罚
            presence_penalty: 存在惩罚
            stop_event: 取消事件
            on_chunk: 可选 ``Callable[[str], None]`` 接收正文增量

        Returns:
            (完整正文, finish_reason 或 None)
        """
        # 延迟导入避免循环；on_chunk 类型用 Any 标注
        if stream:
            parts: list[str] = []
            finish: str | None = None
            async for chunk in self.stream_chat_completion(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                frequency_penalty=frequency_penalty,
                presence_penalty=presence_penalty,
                stop_event=stop_event,
            ):
                if chunk.content:
                    parts.append(chunk.content)
                    if on_chunk is not None:
                        try:
                            on_chunk(chunk.content)
                        except Exception as e:
                            logger.warning("on_chunk 回调异常: %s", e)
                if chunk.finish_reason:
                    finish = chunk.finish_reason
                    break
            return "".join(parts), finish

        response = await self.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens if max_tokens is not None else 2000,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            stop_event=stop_event,
        )
        choice0 = (response.get("choices") or [{}])[0]
        msg = choice0.get("message") or {}
        content = msg.get("content") or ""
        finish = choice0.get("finish_reason")
        if content and on_chunk is not None:
            try:
                on_chunk(content)
            except Exception as e:
                logger.warning("on_chunk 回调异常: %s", e)
        return content, finish

    async def fetch_models(self) -> list[str]:
        """获取可用模型列表（GET /models）。

        Returns:
            模型 ID 列表

        Raises:
            LLMError: 请求失败
        """
        url = f"{self.base_url}/models"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        # 自定义 HTTP 头合并（GET /models 也可能需要自定义头）
        if self.extra_headers:
            headers.update(self.extra_headers)

        if strict_responses.get():
            from novelforge.cli_diagnostics import capture_request
            capture_request({}, headers)
        try:
            session = await self._get_session()
            async with session.get(
                url, headers=headers, timeout=self.timeout,
                proxy=self.proxy or None,
            ) as response:
                if response.status >= 400:
                    body = await response.text()
                    if response.status == 401:
                        raise AuthError("API 认证失败", body, response.headers)
                    if response.status == 429:
                        raise RateLimitError("API 限流", retry_delay(response.headers.get("Retry-After")), body, response.headers)
                    raise APIError(response.status, self._parse_error_body(body), body, response.headers)
                data = await response.json()
                if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                    raise ResponseError("response_schema")
                if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in data["data"]):
                    raise ResponseError("response_schema")
                # 解析 data[].id
                models = []
                for item in data.get("data", []):
                    model_id = item.get("id")
                    if model_id:
                        models.append(model_id)
                logger.info("获取到 %d 个模型", len(models))
                return models
        except aiohttp.ClientError as e:
            raise LLMError(f"获取模型列表网络错误: {e}") from e
