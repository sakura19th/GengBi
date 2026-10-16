"""错误分类、异常因果与脱敏的单元覆盖；不发送外部请求。"""

import argparse
import json
import logging

import pytest

from novelforge.cli_diagnostics import Diagnostics, describe, operation_failed
from novelforge.services.llm_client import APIError, RateLimitError


@pytest.mark.parametrize(
    "body,code",
    [
        (
            '{"error":{"message":"insufficient balance secret-source"}}',
            "quota_exhausted",
        ),
        ('{"error":{"code":"context_length_exceeded"}}', "context_limit"),
        (
            '{"error":{"message":"max_tokens must be <= 4096"}}',
            "output_limit_parameter",
        ),
        ('{"error":{"code":"model_not_found"}}', "model_not_found"),
    ],
)
def test_api_reason_classification_never_echoes_body(body, code):
    failure = describe(APIError(400, "private-key", body))
    assert failure.code == code
    assert "private-key" not in repr(failure)
    assert "secret-source" not in repr(failure)


def test_rate_limit_reports_retry_after():
    failure = describe(RateLimitError("server text includes credential", 2.5))
    assert failure.code == "rate_limited"
    assert failure.details["retry_after_seconds"] == 2.5
    assert "credential" not in failure.message


def test_observed_exception_survives_business_wrapper_without_logging_source(
    tmp_path, capsys
):
    args = argparse.Namespace(
        error_format="json", diagnostics_dir=tmp_path, debug=False
    )
    diagnostics = Diagnostics(args)
    diagnostics.start()
    try:
        diagnostics.phase = "提取请求与响应解析"
        try:
            json.loads("private article and key")
        except json.JSONDecodeError as error:
            logging.getLogger("novelforge.test").warning(
                "解析失败: %s；正文=%s", error, error.doc
            )
        assert diagnostics.report(operation_failed("世界书提取失败")) == 1
    finally:
        diagnostics.close()
    report = json.loads(capsys.readouterr().err)["error"]
    assert report["code"] == "response_json"
    assert report["observed_cause"]["exception_type"] == "JSONDecodeError"
    assert report["details"] == {"line": 1, "column": 1}
    saved = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")
    assert "private article" not in saved


def test_unexpected_exception_is_diagnosed_without_local_variables(tmp_path, capsys):
    diagnostics = Diagnostics(
        argparse.Namespace(error_format="json", diagnostics_dir=tmp_path)
    )
    diagnostics.start()
    try:
        try:
            raise RuntimeError("secret-content-from-provider")
        except RuntimeError as error:
            assert diagnostics.report(error) == 1
    finally:
        diagnostics.close()
    captured = capsys.readouterr().err
    report = json.loads(captured)["error"]
    assert report["code"] == "internal" and report["frames"]
    assert "secret-content-from-provider" not in captured


@pytest.mark.parametrize(
    "value,expected",
    [("invalid", 5), ("nan", 5), ("inf", 5), ("-7", 0), ("1000000", 60), ("2.5", 2.5)],
)
def test_retry_after_is_bounded(value, expected):
    from novelforge.services.llm_client import retry_delay

    assert retry_delay(value) == expected


def test_cleanup_failure_preserves_primary(tmp_path, capsys):
    from novelforge.cli_diagnostics import preserve_failure
    from novelforge.services.llm_client import AuthError

    diagnostics = Diagnostics(
        argparse.Namespace(error_format="json", diagnostics_dir=tmp_path)
    )
    diagnostics.start()
    try:
        with pytest.raises(AuthError):
            try:
                raise AuthError("private-key")
            finally:
                with preserve_failure():
                    raise PermissionError("private-path")
        assert diagnostics.events[-1]["secondary_error"] == "PermissionError"
        with pytest.raises(PermissionError):
            with preserve_failure():
                raise PermissionError("standalone failure")
    finally:
        diagnostics.close()


def test_cleanup_warning_does_not_replace_primary_and_new_phase_clears_it():
    from novelforge.cli_diagnostics import phase
    from novelforge.services.llm_client import AuthError

    diagnostics = Diagnostics()
    diagnostics.start()
    try:
        error = AuthError("private-key")
        logger = logging.getLogger("novelforge.test")
        logger.error("请求失败: %s", error)
        logger.warning("关闭客户端失败: %s", OSError("private"))
        assert describe(operation_failed("提取失败")).code == "authentication"
        phase("保存产物")
        assert diagnostics.observed is None
    finally:
        diagnostics.close()


def test_context_cache_write_failure_is_not_silently_successful(tmp_path):
    import asyncio
    from unittest.mock import AsyncMock, Mock
    from novelforge.core.runtime_mode import strict_cli
    from novelforge.services.context_extractor import ContextExtractor

    extractor = ContextExtractor.__new__(ContextExtractor)
    extractor._storage = Mock()
    extractor._storage.storage = Mock()
    # 业务持久化入口不能吞掉下层异常，即使桌面模式保持容错。
    extractor._get_cached_data = AsyncMock(return_value=None)
    extractor._save_cached_entries = AsyncMock(
        side_effect=PermissionError("private-path")
    )
    token = strict_cli.set(True)
    try:
        with pytest.raises(PermissionError):
            asyncio.run(extractor.save_edited_entries("proj_test", "chap_test", []))
    finally:
        strict_cli.reset(token)


@pytest.mark.parametrize(
    "body", ["", "{}", '{"error":{"details":{"unknown":"do-not-dump"}}}']
)
def test_missing_provider_reason_is_explicit(body):
    failure = describe(APIError(400, "internal fallback", body))
    assert "provider_message_unavailable" in failure.details
    assert "do-not-dump" not in repr(failure)


def test_provider_message_preserves_reason_but_redacts_echoes():
    from novelforge.cli_diagnostics import capture_request

    diagnostics = Diagnostics()
    diagnostics.start()
    try:
        article = (
            "The original confidential article has a very distinctive long sentence."
        )
        capture_request(
            {"messages": [{"role": "user", "content": article}]},
            {"Authorization": "Bearer opaque-credential"},
        )
        error = APIError(
            400,
            "unused",
            json.dumps(
                {
                    "error": {
                        "message": "Unsupported role: "
                        + article[8:52]
                        + " token=opaque-credential",
                        "param": "messages[0].role",
                    }
                }
            ),
        )
        result = describe(error)
        assert "Unsupported role:" in result.details["provider_message"]
        assert "confidential article" not in repr(result)
        assert "opaque-credential" not in repr(result)
        assert result.details["provider_param"] == "messages[0].role"
    finally:
        diagnostics.close()
