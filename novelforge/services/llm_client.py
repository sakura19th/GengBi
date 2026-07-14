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
from dataclasses import dataclass, field
from typing import AsyncIterator

import aiohttp

logger = logging.getLogger(__name__)

# 流式中断保留的最小字符数
MIN_INTERRUPT_CHARS = 100


class LLMError(Exception):
    """LLM API 调用基础异常。"""


class AuthError(LLMError):
    """API 认证失败（401），不重试，需跳转设置。"""


class RateLimitError(LLMError):
    """API 限流（429），含 Retry-After 秒数。"""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class APIError(LLMError):
    """API 返回错误状态码（非 401/429）。"""

    def __init__(self, status: int, message: str, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


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
    ) -> None:
        """初始化 LLM 客户端。

        Args:
            base_url: API 基础 URL（如 ``https://api.openai.com/v1``）
            api_key: API Key
            timeout: 超时秒数（非流式=total 总超时；流式=sock_read 分块间超时）
            reasoning_effort: 思考强度（OpenAI o 系列/DeepSeek V4 等 OpenAI 兼容网关
                支持，取值 auto/minimal/low/medium/high/max；空串/none/off 表示不发送）
            extra_payload: 自定义请求体字段（deep merge 到 payload，如 zenmux
                的 provider_routing_strategy）
            extra_headers: 自定义 HTTP 头（update 到 headers，可覆盖默认头）
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
        # 非流式：total 总超时
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        # 流式：无 total 限制，sock_read 控制分块间超时（检测死连接，不杀长流）
        self.stream_timeout = aiohttp.ClientTimeout(
            total=None, sock_connect=30, sock_read=timeout
        )
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

        xAI Grok 系列不支持 presence_penalty 和 frequency_penalty，
        发送会导致 400 错误。除 grok-3-mini 外的 Grok 模型不支持
        reasoning_effort。
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
        # Gemini 兼容网关兜底：确保至少含一条 user 消息
        messages = self._ensure_user_message(messages)
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
        # 思考强度：根据模型类型解析（Gemini 等模型需映射不支持的值）
        effort = self._resolve_reasoning_effort_for_payload(model)
        if effort:
            payload["reasoning_effort"] = effort
        # 按模型类型过滤不支持的参数（Grok 等）
        self._filter_unsupported_params(payload, model)
        # 自定义 payload 字段 deep merge（如 zenmux provider_routing_strategy）
        if self.extra_payload:
            _deep_merge_dict(payload, self.extra_payload)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        # 自定义 HTTP 头合并（可覆盖默认头）
        if self.extra_headers:
            headers.update(self.extra_headers)

        retry_count = 0
        last_error: Exception | None = None

        while retry_count <= self.MAX_RETRIES:
            try:
                session = await self._get_session()
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.stream_timeout,
                ) as response:
                    # 处理错误状态码（不调用 raise_for_status）
                    if response.status == 401:
                        body = await response.text()
                        logger.error("API 认证失败 (401): %s", body[:200])
                        raise AuthError("API Key 无效，请检查设置")

                    if response.status == 429:
                        retry_after = float(
                            response.headers.get("Retry-After", "5")
                        )
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
                            retry_after,
                        )

                    if response.status >= 400:
                        body = await response.text()
                        error_msg = self._parse_error_body(body)
                        logger.error(
                            "API 错误 (%d): %s", response.status, error_msg
                        )
                        raise APIError(response.status, error_msg, body)

                    # 流式解析 SSE
                    async for chunk in self._parse_sse_stream(
                        response, stop_event
                    ):
                        yield chunk
                    return

            except (AuthError, RateLimitError, APIError):
                raise
            except asyncio.CancelledError:
                logger.info("流式调用被取消")
                raise
            except aiohttp.ClientError as e:
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
            return None

        # 提取 choices[0].delta 内容
        choices = data.get("choices", [])
        if not choices:
            return None

        delta = choices[0].get("delta", {})
        content = delta.get("content", "") or ""
        reasoning_content = delta.get("reasoning_content", "") or ""
        finish_reason = choices[0].get("finish_reason")

        if not content and not reasoning_content and not finish_reason:
            return None

        return StreamChunk(
            content=content,
            reasoning_content=reasoning_content,
            finish_reason=finish_reason,
        )

    @staticmethod
    def _parse_error_body(body: str) -> str:
        """解析 API 错误响应体，提取错误消息。"""
        try:
            data = json.loads(body)
            # OpenAI 格式: {"error": {"message": "..."}}
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

    async def chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        top_p: float = 1.0,
        stop_event: asyncio.Event | None = None,
    ) -> dict:
        """非流式调用 chat/completions，返回完整响应 dict。

        用于上下文提取等需要完整 JSON 响应的场景。
        使用 ``stream: false`` 直接获取完整响应，解析 ``choices[0].message.content``。

        Args:
            messages: 消息列表
            model: 模型名
            temperature: 温度（默认 0.2，低温保证稳定输出）
            max_tokens: 最大生成 token 数
            top_p: top_p 采样
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
        # Gemini 兼容网关兜底：确保至少含一条 user 消息
        messages = self._ensure_user_message(messages)
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
            "top_p": top_p,
            "max_tokens": max_tokens,
        }
        # 思考强度：根据模型类型解析（Gemini 等模型需映射不支持的值）
        effort = self._resolve_reasoning_effort_for_payload(model)
        if effort:
            payload["reasoning_effort"] = effort
        # 按模型类型过滤不支持的参数（Grok 等）
        self._filter_unsupported_params(payload, model)
        # 自定义 payload 字段 deep merge（如 zenmux provider_routing_strategy）
        if self.extra_payload:
            _deep_merge_dict(payload, self.extra_payload)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # 自定义 HTTP 头合并（可覆盖默认头）
        if self.extra_headers:
            headers.update(self.extra_headers)

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
                    timeout=self.timeout,
                ) as response:
                    # 处理错误状态码
                    if response.status == 401:
                        body = await response.text()
                        logger.error("API 认证失败 (401): %s", body[:200])
                        raise AuthError("API Key 无效，请检查设置")

                    if response.status == 429:
                        retry_after = float(
                            response.headers.get("Retry-After", "5")
                        )
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
                            retry_after,
                        )

                    if response.status >= 400:
                        body = await response.text()
                        error_msg = self._parse_error_body(body)
                        logger.error(
                            "API 错误 (%d): %s", response.status, error_msg
                        )
                        raise APIError(response.status, error_msg, body)

                    data = await response.json()
                    logger.debug(
                        "非流式调用成功: model=%s, tokens=%s",
                        model,
                        data.get("usage", {}),
                    )
                    return data

            except (AuthError, RateLimitError, APIError):
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

    async def fetch_models(self) -> list[str]:
        """获取可用模型列表（GET /models）。

        Returns:
            模型 ID 列表

        Raises:
            LLMError: 请求失败
        """
        url = f"{self.base_url}/models"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        # 自定义 HTTP 头合并（GET /models 也可能需要自定义头）
        if self.extra_headers:
            headers.update(self.extra_headers)

        try:
            session = await self._get_session()
            async with session.get(
                url, headers=headers, timeout=self.timeout
            ) as response:
                if response.status >= 400:
                    body = await response.text()
                    raise LLMError(
                        f"获取模型列表失败 ({response.status}): "
                        f"{self._parse_error_body(body)}"
                    )
                data = await response.json()
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
