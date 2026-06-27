# 修复上下文提取流式超时错误

## 概述

上下文提取（流式模式）在长 LLM 响应时抛出 `aiohttp.TimeoutError`，导致整个提取失败、丢失已有批次结果。

**根因**：`LLMClient` 使用 `aiohttp.ClientTimeout(total=120.0)`，`total` 超时覆盖**整个请求生命周期**（含流式响应体读取）。长提取流超过 120 秒即被强制中断。

## 当前状态分析

### 问题 1：超时策略错误（根因）

**文件**：`novelforge/services/llm_client.py`（line 116）

```python
self.timeout = aiohttp.ClientTimeout(total=timeout)  # timeout=120.0
```

- `total` 超时 = 从请求开始到响应体完全接收的总时间
- 流式响应的 body 在长时间内分块到达，`total=120s` 会杀掉合法的长流
- `stream_chat_completion`（line 171）和 `chat_completion`（line 396）都用这个 `self.timeout`

### 问题 2：TimeoutError 未被妥善处理

**文件**：`novelforge/services/context_extractor.py`

- `extract()`（line 848）和 `extract_streaming()`（line ~1170）的异常处理链：
  ```python
  except asyncio.CancelledError: ...
  except (AuthError, RateLimitError, APIError, LLMError) as e: ...
  except Exception as e:  # TimeoutError 落入此处，直接返回失败，丢失已有批次结果
  ```
- `asyncio.TimeoutError` 不是 `LLMError` 子类，落入通用 `Exception` 分支，整个提取失败、已有批次条目全部丢失

### 问题 3：提取器使用默认超时

**文件**：`novelforge/services/context_extractor.py`（line 665）

```python
client = LLMClient(base_url=base_url, api_key=api_key)  # 默认 timeout=120s
```

未传入更大的超时值。

## 实施方案

### Change 1：分离流式/非流式超时策略

**文件**：`novelforge/services/llm_client.py`

**修改 `__init__`**（line 101-116）：

```python
def __init__(
    self,
    base_url: str,
    api_key: str,
    timeout: float = 300.0,
) -> None:
    """初始化 LLM 客户端。

    Args:
        base_url: API 基础 URL
        api_key: API Key
        timeout: 超时秒数（非流式=total 总超时；流式=sock_read 分块间超时）
    """
    self.base_url = base_url.rstrip("/")
    self.api_key = api_key
    # 非流式：total 总超时
    self.timeout = aiohttp.ClientTimeout(total=timeout)
    # 流式：无 total 限制，sock_read 控制分块间超时（检测死连接，不杀长流）
    self.stream_timeout = aiohttp.ClientTimeout(
        total=None, sock_connect=30, sock_read=timeout
    )
```

**修改 `stream_chat_completion`**（line 171）：

```python
# 旧：async with aiohttp.ClientSession(timeout=self.timeout) as session:
# 新：
async with aiohttp.ClientSession(timeout=self.stream_timeout) as session:
```

**决策说明**：
- 默认 `timeout` 从 120s 提升至 300s（非流式总超时）
- 流式：`total=None`（无总时长限制）+ `sock_read=300s`（两个 chunk 之间最多等 5 分钟，检测死连接）
- `sock_connect=30s`（连接建立超时，快速失败）
- 非流式 `chat_completion` 和 `fetch_models` 继续用 `self.timeout`（total=300s）

### Change 2：提取器传入更大超时

**文件**：`novelforge/services/context_extractor.py`（line 665）

```python
# 旧：client = LLMClient(base_url=base_url, api_key=api_key)
# 新：
client = LLMClient(base_url=base_url, api_key=api_key, timeout=300)
```

### Change 3：捕获 TimeoutError 并重试批次

**文件**：`novelforge/services/context_extractor.py`

#### 3a. `extract_streaming()` 流式调用块（line 1133-1170 附近）

将 LLM 流式调用 + 解析包裹在重试循环中（最多重试 1 次）。超时重试失败时，保留已有批次条目并返回部分结果。

```python
# 流式调用 LLM（超时重试 1 次）
messages = [{"role": "user", "content": prompt}]
content_parts: list[str] = []
extract_timeout_retried = False

while True:
    try:
        async for chunk in client.stream_chat_completion(
            messages=messages,
            model=model,
            temperature=EXTRACT_TEMPERATURE,
            max_tokens=EXTRACT_MAX_TOKENS,
            stop_event=self._cancel_event,
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
        break  # 成功，退出重试循环
    except asyncio.CancelledError:
        logger.info("上下文提取被取消")
        return ExtractResult(
            entries=[],
            status="failed",
            error="用户取消提取",
            elapsed_seconds=time.time() - start_time,
        )
    except asyncio.TimeoutError as e:
        if not extract_timeout_retried:
            logger.warning(
                "流式批次 %d/%d 超时，重试中...", batch_idx + 1, batch_count
            )
            extract_timeout_retried = True
            content_parts.clear()
            continue
        # 重试仍失败：保留已有批次结果，返回部分结果
        logger.error(
            "流式批次 %d/%d 超时重试失败: %s", batch_idx + 1, batch_count, e
        )
        return ExtractResult(
            entries=all_entries,  # 保留已有批次条目
            status="failed",
            error=f"批次 {batch_idx + 1}/{batch_count} 超时（已重试），"
                  f"已保留前 {len(all_entries)} 条结果",
            elapsed_seconds=time.time() - start_time,
            token_usage=total_token_usage,
        )
    except (AuthError, RateLimitError, APIError, LLMError) as e:
        logger.error("上下文提取 LLM 调用失败: %s", e)
        return ExtractResult(
            entries=[],
            status="failed",
            error=str(e),
            elapsed_seconds=time.time() - start_time,
        )
    except Exception as e:
        logger.error("上下文提取异常: %s", e, exc_info=True)
        return ExtractResult(
            entries=[],
            status="failed",
            error=f"提取异常: {e}",
            elapsed_seconds=time.time() - start_time,
        )
```

#### 3b. `extract()` 非流式调用块（line 824-855 附近）

同样的重试逻辑应用于 `chat_completion` 调用：

```python
# 调用 LLM（超时重试 1 次）
messages = [{"role": "user", "content": prompt}]
extract_timeout_retried = False

while True:
    try:
        response = await client.chat_completion(
            messages=messages,
            model=model,
            temperature=EXTRACT_TEMPERATURE,
            max_tokens=EXTRACT_MAX_TOKENS,
            stop_event=self._cancel_event,
        )
        break
    except asyncio.CancelledError:
        logger.info("上下文提取被取消")
        return ExtractResult(
            entries=[],
            status="failed",
            error="用户取消提取",
            elapsed_seconds=time.time() - start_time,
        )
    except asyncio.TimeoutError as e:
        if not extract_timeout_retried:
            logger.warning(
                "批次 %d/%d 超时，重试中...", batch_idx + 1, batch_count
            )
            extract_timeout_retried = True
            continue
        logger.error(
            "批次 %d/%d 超时重试失败: %s", batch_idx + 1, batch_count, e
        )
        return ExtractResult(
            entries=all_entries,
            status="failed",
            error=f"批次 {batch_idx + 1}/{batch_count} 超时（已重试），"
                  f"已保留前 {len(all_entries)} 条结果",
            elapsed_seconds=time.time() - start_time,
            token_usage=total_token_usage,
        )
    except (AuthError, RateLimitError, APIError, LLMError) as e:
        logger.error("上下文提取 LLM 调用失败: %s", e)
        return ExtractResult(
            entries=[],
            status="failed",
            error=str(e),
            elapsed_seconds=time.time() - start_time,
        )
    except Exception as e:
        logger.error("上下文提取异常: %s", e, exc_info=True)
        return ExtractResult(
            entries=[],
            status="failed",
            error=f"提取异常: {e}",
            elapsed_seconds=time.time() - start_time,
        )
```

## 假设与决策

1. **流式用 `sock_read` 而非 `total`**：`total` 超时对长流式响应是错误模型——流可以合法运行数分钟。`sock_read`（两 chunk 间隔超时）才是检测死连接的正确方式。
2. **默认超时 300s**：从 120s 提升。非流式 total=300s 足够大请求完成；流式 sock_read=300s 足够慢模型生成下一段。
3. **重试 1 次**：网络抖动导致的偶发超时，重试一次即可恢复。不无限重试（避免卡死）。
4. **超时重试失败保留部分结果**：`entries=all_entries`（已有批次条目）而非 `entries=[]`，避免丢失已完成的工作。`status="failed"` 但带已有条目，UI 可显示部分结果。
5. **不新增配置项**：300s 是合理默认值，无需暴露给用户配置。避免过度工程化。
6. **`extract_timeout_retried` 标志在批次循环内重置**：每个批次独立重试，不是全局只重试一次。

## 验证步骤

1. 运行测试套件：`cd /workspace && QT_QPA_PLATFORM=offscreen python -m pytest tests/ -x -q`
2. 验证导入：`cd /workspace && QT_QPA_PLATFORM=offscreen python -c "from novelforge.services.llm_client import LLMClient; from novelforge.services.context_extractor import ContextExtractor; print('OK')"`
3. 重点验证：
   - `test_llm_client.py`（如有超时相关测试）
   - `test_m4_context_extraction.py`（提取流程不回归）
