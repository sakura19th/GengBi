"""高级配置的显式文件输入、类型校验及脱敏显示。"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from novelforge.cli_workspace import emit, require

ACTIONS = {"settings", "endpoint-options", "copy", "set-jailbreak", "model-prompt"}


def add_commands(actions: Any, common: argparse.ArgumentParser) -> None:
    model_prompt = actions.add_parser(
        "model-prompt", parents=[common], help="按端点和完整模型 ID 查看、设置或清除附加 system 提示词"
    )
    model_prompt.add_argument("--endpoint", required=True, help="端点 ID")
    model_prompt.add_argument("--model", required=True, help="完整模型 ID，区分大小写，不支持通配符")
    change = model_prompt.add_mutually_exclusive_group()
    change.add_argument("-i", "--input", type=Path, help="UTF-8 提示词文件；省略只查看")
    change.add_argument("--clear", action="store_true", help="删除该模型的附加提示词")
    settings = actions.add_parser(
        "settings",
        parents=[common],
        help="查看或用 JSON 更新写作、提取、卷和网络默认设置",
    )
    settings.add_argument(
        "section", choices=["continuation", "context_extract", "volume", "network"]
    )
    settings.add_argument(
        "-i", "--input", type=Path, help="JSON 对象，按字段合并；省略只查看"
    )
    endpoint = actions.add_parser(
        "endpoint-options",
        parents=[common],
        help="端点模型列表、推理强度和请求扩展；显示时脱敏",
    )
    endpoint.add_argument("id")
    endpoint.add_argument(
        "-i",
        "--input",
        type=Path,
        help="JSON 对象；允许 models/enabled_models/default_model/reasoning_effort/merge_system_messages/extra_payload/extra_headers",
    )
    copy = actions.add_parser(
        "copy", parents=[common], help="复制端点，包括本机加密密钥"
    )
    copy.add_argument("source")
    copy.add_argument("--id", required=True)
    copy.add_argument("--name")
    jailbreak = actions.add_parser(
        "set-jailbreak", parents=[common], help="保存非正文流程的破限等级或自定义文本"
    )
    jailbreak.add_argument("flow")
    jailbreak.add_argument("level", choices=["off", "low", "mid", "high", "custom"])
    jailbreak.add_argument("--text-file", type=Path, help="custom 时必填，UTF-8 文本")


def object_file(path: Path) -> dict:
    from novelforge.services.headless import read_json

    value = read_json(path)
    if not isinstance(value, dict):
        raise TypeError("设置文件必须是 JSON 对象")
    return value


def execute(args: argparse.Namespace, config: Any) -> None:
    import copy
    from urllib.parse import urlsplit, urlunsplit

    from novelforge.core.config import FLOW_DEFINITIONS, get_default_config
    from novelforge.models import VolumeRunConfig
    from novelforge.services.headless import _number
    from novelforge.utils.ids import validate_id

    if args.action == "model-prompt":
        from novelforge.services.model_prompts import prompt_summary, validate_model_prompts

        endpoint = require(config.get_endpoint(args.endpoint), "端点")
        if not args.model.strip() or args.model != args.model.strip():
            raise ValueError("模型 ID 不能为空或含首尾空格")
        prompts = validate_model_prompts(endpoint.get("model_prompts", {}))
        if args.input:
            prompts[args.model] = args.input.read_text(encoding="utf-8-sig")
            validate_model_prompts(prompts)
        elif args.clear:
            prompts.pop(args.model, None)
        if args.input or args.clear:
            config.update_endpoint(args.endpoint, {"model_prompts": prompts})
        emit({"endpoint": args.endpoint, "saved": bool(args.input or args.clear),
              **prompt_summary(prompts, args.model)})
        return
    if args.action == "copy":
        validate_id(args.id)
        if config.get_endpoint(args.id):
            raise ValueError("目标端点 ID 已存在，请使用新的 ID")
        endpoint = copy.deepcopy(require(config.get_endpoint(args.source), "源端点"))
        endpoint.update(
            id=args.id, name=args.name or endpoint.get("name", "") + " 副本"
        )
        config.add_endpoint(endpoint)
        emit({"copied": True, "id": args.id})
        return
    if args.action == "set-jailbreak":
        if args.flow not in {key for key, _label in FLOW_DEFINITIONS}:
            raise ValueError("未知流程 ID，请先运行 config show")
        if args.flow in {"single_continuation", "volume_continuation"}:
            raise ValueError(
                "正文破限由预设控制，请使用 continue/volume/flows 的 --jailbreak，或 preset prompt-toggle"
            )
        if args.level == "custom":
            if not args.text_file:
                raise ValueError("custom 需要 --text-file")
            text = args.text_file.read_text(encoding="utf-8-sig")
            if not text.strip():
                raise ValueError("自定义文本不能为空")
            config.set_flow_jailbreaks_custom(
                {**config.config.get("flow_jailbreaks_custom", {}), args.flow: text}
            )
        config.set_flow_jailbreaks(
            {**config.get_flow_jailbreaks(), args.flow: args.level}
        )
        emit({"saved": True, "flow": args.flow, "level": args.level})
        return
    if args.action == "endpoint-options":
        endpoint = require(config.get_endpoint(args.id), "端点")
        allowed = {
            "models",
            "enabled_models",
            "default_model",
            "reasoning_effort",
            "merge_system_messages",
            "extra_payload",
            "extra_headers",
        }
        if args.input:
            update = object_file(args.input)
            if set(update) - allowed:
                raise ValueError(
                    "端点选项包含未知字段：" + ", ".join(sorted(set(update) - allowed))
                )
            for key in {"models", "enabled_models"} & update.keys():
                if not isinstance(update[key], list) or any(
                    not isinstance(v, str) or not v for v in update[key]
                ):
                    raise ValueError(f"{key} 必须是非空模型名称组成的数组")
            if "default_model" in update and (
                not isinstance(update["default_model"], str)
                or not update["default_model"]
            ):
                raise ValueError("default_model 必须是非空字符串")
            if "reasoning_effort" in update and update["reasoning_effort"] not in {
                "",
                "none",
                "auto",
                "minimal",
                "low",
                "medium",
                "high",
                "max",
            }:
                raise ValueError("未知 reasoning_effort")
            if "merge_system_messages" in update and not isinstance(
                update["merge_system_messages"], bool
            ):
                raise ValueError("merge_system_messages 必须是布尔值")
            for key in {"extra_payload", "extra_headers"} & update.keys():
                if not isinstance(update[key], dict):
                    raise TypeError(f"{key} 必须是 JSON 对象")
            if "extra_headers" in update and any(
                not isinstance(v, str) or "\r" in v or "\n" in v
                for v in update["extra_headers"].values()
            ):
                raise ValueError("请求头的值必须是不含换行的字符串")
            if (
                "enabled_models" in update
                and update["enabled_models"]
                and "default_model" not in update
            ):
                update["default_model"] = update["enabled_models"][0]
            config.update_endpoint(args.id, update)
            endpoint = config.get_endpoint(args.id)
        emit(
            {
                "id": args.id,
                **{
                    key: endpoint.get(key)
                    for key in sorted(allowed - {"extra_headers", "extra_payload"})
                },
                "model_prompt_models": sorted(endpoint.get("model_prompts", {})),
                "extra_header_keys": sorted(
                    (endpoint.get("extra_headers") or {}).keys()
                ),
                "extra_payload_keys": sorted(
                    (endpoint.get("extra_payload") or {}).keys()
                ),
            }
        )
        return
    section = args.section
    current = copy.deepcopy(config.config.get(section, {}))
    if args.input:
        update = object_file(args.input)
        allowed = (
            set(VolumeRunConfig.model_fields)
            if section == "volume"
            else set(get_default_config()[section])
        )
        if set(update) - allowed:
            raise ValueError(
                "未知配置字段：" + ", ".join(sorted(set(update) - allowed))
            )
        for key, value in update.items():
            default = get_default_config().get(section, {}).get(key)
            if isinstance(default, bool) and not isinstance(value, bool):
                raise TypeError(f"{key} 必须是布尔值")
            if isinstance(default, str) and not isinstance(value, str):
                raise TypeError(f"{key} 必须是字符串")
            if isinstance(default, list) and (
                not isinstance(value, list)
                or any(not isinstance(item, str) for item in value)
            ):
                raise ValueError(f"{key} 必须是字符串数组")
            if isinstance(default, dict) and (
                not isinstance(value, dict)
                or any(not isinstance(item, str) for item in value.values())
            ):
                raise ValueError(f"{key} 必须是字符串值的对象")
        ranges = {
            "default_lookback_chapters": (0, 1000000, True),
            "default_target_words": (100, 50000, True),
            "default_temperature": (0, 2, False),
            "user_input_history_size": (1, 30, True),
            "cache_ttl_hours": (0, 1000000, True),
            "lookback_chapters": (0, 1000000, True),
            "token_limit": (0, 100000000, True),
        }
        for key, (low, high, integer) in ranges.items():
            if key in update:
                _number(update[key], key, low, high, integer=integer)
        if (
            "extractor_prompt_override" in update
            and update["extractor_prompt_override"] is not None
            and not isinstance(update["extractor_prompt_override"], str)
        ):
            raise ValueError("extractor_prompt_override 必须是文本或 null")
        current.update(update)
        if section == "volume":
            current = VolumeRunConfig.model_validate(current).model_dump(mode="json")
        if section == "network" and current.get("http_proxy"):
            url = urlsplit(current["http_proxy"])
            if url.scheme not in {"http", "https"} or not url.hostname:
                raise ValueError("代理地址须为 HTTP(S) URL")
        config.config[section] = current
        config.save()
    if section == "network" and current.get("http_proxy"):
        parsed = urlsplit(current["http_proxy"])
        if parsed.username or parsed.password:
            current["http_proxy"] = urlunsplit(
                (
                    parsed.scheme,
                    "[凭据已隐藏]@" + parsed.netloc.rsplit("@", 1)[-1],
                    parsed.path,
                    "",
                    "",
                )
            )
    emit({"section": section, "settings": current, "saved": bool(args.input)})
