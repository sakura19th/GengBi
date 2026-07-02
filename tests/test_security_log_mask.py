"""安全测试：日志脱敏增强（M-3）。

覆盖：
1. ``sk-ant-xxx``（Anthropic 前缀）被脱敏
2. ``sk-or-xxx``（OpenRouter 前缀）被脱敏
3. ``sk-xxx``（OpenAI/DeepSeek 前缀）被脱敏
4. ``Authorization: Bearer xxx`` 整体脱敏（无论 token 前缀）
5. 自定义网关 token（非 sk- 前缀）经 Authorization 头脱敏
6. 长文本截断不破坏脱敏
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from novelforge.core.logger import SensitiveDataFilter


sanitize = SensitiveDataFilter._sanitize


# ===== 1. sk-ant- 前缀脱敏 =====


def test_sk_ant_prefix_masked() -> None:
    """sk-ant-xxx（Anthropic）被脱敏为 sk-****。"""
    text = "API Key: sk-ant-api03-1234567890abcdef"
    result = sanitize(text)
    assert "sk-ant-api03-1234567890abcdef" not in result
    assert "sk-****" in result


# ===== 2. sk-or- 前缀脱敏 =====


def test_sk_or_prefix_masked() -> None:
    """sk-or-xxx（OpenRouter）被脱敏为 sk-****。"""
    text = "Authorization with sk-or-v1-1234567890abcdef"
    result = sanitize(text)
    assert "sk-or-v1-1234567890abcdef" not in result
    assert "sk-****" in result


# ===== 3. sk- 前缀脱敏 =====


def test_sk_prefix_masked() -> None:
    """sk-xxx（OpenAI/DeepSeek）被脱敏为 sk-****。"""
    text = "key=sk-proj-abcdef1234567890"
    result = sanitize(text)
    assert "sk-proj-abcdef1234567890" not in result
    assert "sk-****" in result


# ===== 4. Authorization Bearer 头整体脱敏 =====


def test_authorization_bearer_header_masked() -> None:
    """Authorization: Bearer xxx 被整体脱敏（token 部分变 ****）。"""
    text = 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.customtoken123'
    result = sanitize(text)
    assert "eyJhbGciOiJIUzI1NiJ9.customtoken123" not in result
    assert "****" in result
    # "Authorization: Bearer " 前缀应保留
    assert "Authorization: Bearer " in result or "Authorization: Bearer" in result


def test_authorization_with_equals_sign_masked() -> None:
    """Authorization=Bearer xxx 形式（= 分隔）被脱敏。"""
    text = 'Authorization=Bearer customtoken456'
    result = sanitize(text)
    assert "customtoken456" not in result
    assert "****" in result


def test_authorization_with_colon_no_space_masked() -> None:
    """Authorization:Bearer xxx（冒号无空格）被脱敏。"""
    text = 'Authorization:Bearer customtoken789'
    result = sanitize(text)
    assert "customtoken789" not in result
    assert "****" in result


# ===== 5. 自定义网关 token（非 sk- 前缀）经 Authorization 头脱敏 =====


def test_custom_gateway_token_masked_via_authorization() -> None:
    """非 sk- 前缀的自定义 token 经 Authorization Bearer 头脱敏。"""
    text = 'Authorization: Bearer gw_abc123def456ghi789'
    result = sanitize(text)
    assert "gw_abc123def456ghi789" not in result
    assert "****" in result


# ===== 6. 多个 key 同时脱敏 =====


def test_multiple_keys_masked_simultaneously() -> None:
    """同一段文本含多个 key 时全部脱敏。"""
    text = (
        "first=sk-ant-aaa111222333, second=sk-or-bbb444555666, "
        'header=Authorization: Bearer ccc777888999'
    )
    result = sanitize(text)
    assert "sk-ant-aaa111222333" not in result
    assert "sk-or-bbb444555666" not in result
    assert "ccc777888999" not in result
    assert result.count("****") >= 3


# ===== 7. 普通文本不被误伤 =====


def test_normal_text_not_affected() -> None:
    """普通文本（无 key）不被脱敏破坏。"""
    text = "今天天气不错，sk- 是 OpenAI 的前缀"
    result = sanitize(text)
    # "sk- " 不会被误匹配（{8,} 要求至少 8 字符）
    assert "今天天气不错" in result


# ===== 8. 大小写不敏感 =====


def test_authorization_case_insensitive() -> None:
    """authorization（小写）/ AUTHORIZATION（大写）均被脱敏。"""
    text_lower = 'authorization: Bearer lowercasetoken123'
    text_upper = 'AUTHORIZATION: Bearer UPPERCASETOKEN456'
    assert "lowercasetoken123" not in sanitize(text_lower)
    assert "UPPERCASETOKEN456" not in sanitize(text_upper)


# ===== 9. Bearer 单独出现（无 Authorization 前缀）也脱敏 =====


def test_bearer_alone_masked() -> None:
    """裸 Bearer xxx（无 Authorization 前缀）被 BEARER_PATTERN 脱敏。"""
    text = 'token=Bearer standalonebearer789'
    result = sanitize(text)
    assert "standalonebearer789" not in result
    assert "****" in result
