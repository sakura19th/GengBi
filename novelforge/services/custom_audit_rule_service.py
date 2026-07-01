"""自定义设定/审计必查项 AI 结构化服务。

用户输入文本 → AI 结合世界观底层与上下文结构化为 ``CustomAuditRule``，
固化到 ``Project.custom_audit_rules``。

参考 ``OntologyExtractor`` 与 ``ContextExtractor`` 的流式模式：
``stream_chat_completion`` 真流式（逐 chunk 推送到 UI）+ 2 次重试
（温度 0.2/0.0）+ stop_event 跨线程取消 +
协程内 ``await self.storage_service.storage.save_project`` 直连异步层持久化 +
finally ``await client.close()`` 释放 aiohttp session。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime
from typing import Any, Callable

from novelforge.core.config import ConfigManager
from novelforge.core.json_utils import strip_markdown_fences
from novelforge.models import Project
from novelforge.models.custom_audit_rule import CustomAuditRule
from novelforge.services.llm_client import (
    APIError,
    AuthError,
    LLMClient,
    LLMError,
    RateLimitError,
)
from novelforge.services.storage_service import StorageService
from novelforge.utils.ids import generate_id
from novelforge.utils.paths import get_agent_prompt_path, load_text_resource

logger = logging.getLogger(__name__)

# 结构化请求的 max_tokens（足够返回 4 字段 JSON）
CUSTOM_RULE_PARSE_MAX_TOKENS = 2000

# 解析温度（低温保证稳定输出）
CUSTOM_RULE_PARSE_TEMPERATURE = 0.2
CUSTOM_RULE_PARSE_TEMPERATURE_RETRY = 0.0


class CustomAuditRuleService:
    """自定义设定/审计必查项 AI 结构化服务。

    将用户输入的任意文本结合世界观底层与上下文条目，交给 LLM 结构化为
    ``{title, requirement, audit_criteria, severity}`` 四字段 JSON，
    解析为 ``CustomAuditRule`` 持久化到 ``Project.custom_audit_rules``。
    """

    def __init__(
        self,
        storage_service: StorageService,
        config_manager: ConfigManager,
    ) -> None:
        self.storage_service = storage_service
        self.config_manager = config_manager
        self._prompt_template: str | None = None

    # ===== 模板加载 =====

    def _load_prompt_template(self) -> str:
        """加载自定义设定结构化提示词模板（带缓存）。"""
        if self._prompt_template is None:
            path = get_agent_prompt_path("custom_rule_parse")
            try:
                self._prompt_template = load_text_resource(path)
            except OSError as e:
                logger.error("加载自定义设定结构化提示词模板失败: %s", e)
                # 回退最小可用模板
                self._prompt_template = (
                    "请将以下用户输入结构化为自定义设定/审计必查项。\n\n"
                    "# 用户输入\n{{raw_input}}\n\n"
                    "# 世界观底层\n{{world_ontology}}\n\n"
                    "# 上下文条目\n{{context_entries}}\n\n"
                    "请输出严格 JSON 对象，含字段："
                    "title（简短标题）/requirement（续写生成约束）/"
                    "audit_criteria（审计检查向）/severity（critical 或 major）。"
                    "只输出 JSON，不要 markdown 代码块。"
                )
        return self._prompt_template

    # ===== LLM 客户端 =====

    def _get_llm_client(self, flow_key: str = "") -> tuple[LLMClient, str] | None:
        """获取 LLM 客户端与默认模型。

        Args:
            flow_key: 流程键（如 "custom_rule_parsing"），空串用默认端点
        """
        if flow_key:
            ep = self.config_manager.get_flow_endpoint(flow_key)
        else:
            ep = self.config_manager.get_default_endpoint()
        if not ep:
            logger.error("未配置 API 端点，无法结构化自定义设定")
            return None

        api_key = self.config_manager.decrypt_api_key(ep.get("id", ""))
        if not api_key:
            logger.error("API Key 无效，无法结构化自定义设定")
            return None

        base_url = ep.get("base_url", "")
        if not base_url:
            logger.error("API base_url 为空，无法结构化自定义设定")
            return None

        reasoning_effort = ep.get("reasoning_effort", "") or ""
        client = LLMClient(
            base_url=base_url,
            api_key=api_key,
            timeout=120,
            reasoning_effort=reasoning_effort,
        )
        model = ep.get("default_model", "")
        return (client, model)

    # ===== 占位符格式化 =====

    @staticmethod
    def _format_world_ontology(wo: Any) -> str:
        """格式化 WorldOntology 为 JSON 字符串（None → 占位文本）。"""
        if wo is None:
            return "（无世界观底层）"
        try:
            if hasattr(wo, "model_dump"):
                return json.dumps(wo.model_dump(mode="json"), ensure_ascii=False, indent=2)
            return json.dumps(wo, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.warning("WorldOntology 序列化失败: %s", e)
            return "（世界观底层序列化失败）"

    @staticmethod
    def _format_context_entries(entries: list[Any] | None) -> str:
        """格式化上下文条目为可读 Markdown（空 → 占位文本）。"""
        if not entries:
            return "（无上下文条目）"
        parts: list[str] = []
        for entry in entries:
            content = getattr(entry, "content", "") if not isinstance(entry, dict) else entry.get("content", "")
            keys = getattr(entry, "keys", "") if not isinstance(entry, dict) else entry.get("keys", "")
            comment = getattr(entry, "comment", "") if not isinstance(entry, dict) else entry.get("comment", "")
            label = comment or keys or "条目"
            if content:
                parts.append(f"- [{label}] {content}")
        return "\n".join(parts) if parts else "（无上下文条目）"

    def _build_prompt(
        self,
        raw_input: str,
        world_ontology: Any,
        context_entries: list[Any] | None,
    ) -> str:
        """构建提示词（str.replace 宏替换 3 个占位符）。"""
        template = self._load_prompt_template()
        prompt = template.replace("{{raw_input}}", raw_input or "（空输入）")
        prompt = prompt.replace(
            "{{world_ontology}}", self._format_world_ontology(world_ontology)
        )
        prompt = prompt.replace(
            "{{context_entries}}", self._format_context_entries(context_entries)
        )
        return prompt

    # ===== 响应解析 =====

    @staticmethod
    def _parse_response(content: str) -> dict[str, Any]:
        """解析 LLM 响应为 dict（去 markdown fence + JSON 解析）。"""
        cleaned = strip_markdown_fences(content).strip()
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise ValueError("响应不是 JSON 对象")
        # 仅保留 4 字段 + severity 容错
        result = {
            "title": str(data.get("title", "")).strip(),
            "requirement": str(data.get("requirement", "")).strip(),
            "audit_criteria": str(data.get("audit_criteria", "")).strip(),
            "severity": str(data.get("severity", "critical")).strip().lower(),
        }
        if result["severity"] not in ("critical", "major"):
            result["severity"] = "critical"
        return result

    # ===== 主流程 =====

    async def parse_rule_streaming(
        self,
        project: Project,
        raw_input: str,
        context_entries: list[Any] | None = None,
        on_chunk: Callable[[str], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> tuple[CustomAuditRule | None, str]:
        """流式解析用户输入为 CustomAuditRule。

        流程：
        1. 加载 phase_custom_rule_parse.txt 模板，注入 3 个占位符
        2. 调用 LLM（``stream_chat_completion`` 真流式，2 次重试：温度 0.2 / 0.0），
           ``on_chunk`` 逐 chunk 推送内容到 UI
        3. 累积完整 content 后解析 JSON → CustomAuditRule
        4. 协程内 ``await self.storage_service.storage.save_project`` 直连异步层持久化
        5. finally ``await client.close()`` 释放 aiohttp session

        Args:
            project: 项目对象（含 world_ontology 与 custom_audit_rules）
            raw_input: 用户原始输入文本
            context_entries: 上下文条目列表（可选）
            on_chunk: 内容回调（供 UI 显示 AI 结构化过程）
            stop_event: 取消事件

        Returns:
            (CustomAuditRule | None, 状态消息) 元组
        """
        if not raw_input or not raw_input.strip():
            return None, "输入为空"

        client_info = self._get_llm_client("custom_rule_parsing")
        if client_info is None:
            return None, "未配置 API 端点或 API Key 无效"
        client, model = client_info

        try:
            prompt = self._build_prompt(
                raw_input, project.world_ontology, context_entries
            )
            messages = [{"role": "user", "content": prompt}]

            temperatures = [
                CUSTOM_RULE_PARSE_TEMPERATURE,
                CUSTOM_RULE_PARSE_TEMPERATURE_RETRY,
            ]
            last_error = ""
            parsed: dict[str, Any] | None = None
            content = ""

            for attempt, temperature in enumerate(temperatures):
                if stop_event is not None and stop_event.is_set():
                    return None, "用户取消"
                try:
                    # 真流式：逐 chunk 累积 + on_chunk 推送到 UI
                    content_parts: list[str] = []
                    async for chunk in client.stream_chat_completion(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=CUSTOM_RULE_PARSE_MAX_TOKENS,
                        stop_event=stop_event,
                    ):
                        if chunk.content:
                            content_parts.append(chunk.content)
                            if on_chunk is not None:
                                try:
                                    on_chunk(chunk.content)
                                except Exception as e:
                                    logger.warning("on_chunk 回调异常: %s", e)
                        if chunk.finish_reason:
                            break
                    if stop_event is not None and stop_event.is_set():
                        return None, "用户取消"
                    content = "".join(content_parts)
                    if not content:
                        last_error = "响应内容为空"
                        logger.warning(
                            "自定义设定解析第 %d 次尝试响应内容为空", attempt + 1
                        )
                        if attempt < len(temperatures) - 1:
                            continue
                        return None, last_error

                    parsed = self._parse_response(content)
                    break  # 成功
                except asyncio.CancelledError:
                    logger.info("自定义设定解析被取消（第 %d 次尝试）", attempt + 1)
                    return None, "用户取消"
                except json.JSONDecodeError as e:
                    last_error = f"JSON 解析失败: {e}"
                    logger.warning(
                        "自定义设定解析第 %d 次尝试 JSON 解析失败: %s, content=%s",
                        attempt + 1, e, content[:200],
                    )
                    if attempt < len(temperatures) - 1:
                        continue
                    return None, last_error
                except asyncio.TimeoutError as e:
                    last_error = f"超时: {e}"
                    logger.warning("自定义设定解析第 %d 次尝试超时: %s", attempt + 1, e)
                    if attempt < len(temperatures) - 1:
                        continue
                    return None, last_error
                except (AuthError, RateLimitError, APIError, LLMError) as e:
                    last_error = f"LLM 调用失败: {e}"
                    logger.warning("自定义设定解析第 %d 次尝试 LLM 调用失败: %s", attempt + 1, e)
                    if attempt < len(temperatures) - 1:
                        continue
                    return None, last_error
                except Exception as e:
                    last_error = f"解析异常: {e}"
                    logger.error(
                        "自定义设定解析第 %d 次尝试异常: %s",
                        attempt + 1, e, exc_info=True,
                    )
                    if attempt < len(temperatures) - 1:
                        continue
                    return None, last_error

            if parsed is None:
                return None, last_error or "解析失败"

            # 构造 CustomAuditRule
            rule = CustomAuditRule(
                id=generate_id("nf_rule_"),
                title=parsed["title"] or "未命名设定",
                raw_input=raw_input,
                requirement=parsed["requirement"],
                audit_criteria=parsed["audit_criteria"],
                severity=parsed["severity"],
                created_at=datetime.now(),
            )

            # 追加到 project.custom_audit_rules 并持久化
            if not isinstance(project.custom_audit_rules, list):
                project.custom_audit_rules = []
            project.custom_audit_rules.append(rule)
            project.updated_at = datetime.now()
            try:
                await self.storage_service.storage.save_project(
                    project.model_dump(mode="json")
                )
                logger.info("自定义设定已保存: %s (%s)", rule.title, rule.id)
            except Exception as e:
                logger.error("自定义设定持久化失败: %s", e, exc_info=True)
                return None, f"持久化失败: {e}"

            return rule, f"已新增自定义设定：{rule.title}"
        finally:
            # 释放 aiohttp session，避免 Unclosed client session 警告
            try:
                await client.close()
            except Exception as e:
                logger.warning("关闭 LLMClient 失败: %s", e)
