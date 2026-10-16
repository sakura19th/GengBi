"""端点内按完整模型 ID 配置的附加任务说明。默认不附加任何文本。"""

from __future__ import annotations


def validate_model_prompts(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("model_prompts 必须是模型 ID 到非空提示词的 JSON 对象")
    for model, prompt in value.items():
        if not isinstance(model, str) or not model.strip() or model != model.strip():
            raise ValueError("model_prompts 的模型 ID 必须是非空字符串且不含首尾空格")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("model_prompts 的提示词必须是非空字符串；停用请用 --clear")
    return dict(value)


def prompt_summary(prompts: dict[str, str], model: str) -> dict:
    """预览单独列出发送时附加的文本，不改动上游组装结果。"""
    text = prompts.get(model, "")
    return {"model": model, "enabled": bool(text), "role": "system", "text": text}
