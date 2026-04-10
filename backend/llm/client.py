import json
import logging
import time

from openai import OpenAI, APIConnectionError, APITimeoutError, RateLimitError

from .exceptions import LLMJSONParseError, LLMRequestError
from .providers import ProviderConfig, get_provider

logger = logging.getLogger('llm')

DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 12.0    # 单次 LLM 调用超时（秒）；所有调用方应使用此常量
LLM_DEFAULT_TIMEOUT = DEFAULT_TIMEOUT   # 公开别名，供其他模块 import 使用
BACKOFF_BASE = 1  # seconds
BACKOFF_CAP = 30  # seconds


class LLMClient:
    """Unified LLM client that works with any OpenAI-compatible provider."""

    def __init__(self, provider=None, config=None, timeout=None, max_retries=None):
        """Initialize client.

        Args:
            provider: Provider name string (loaded from settings).
            config: ProviderConfig instance (takes precedence over provider).
            timeout: Request timeout in seconds (default 60).
            max_retries: Max retry attempts on transient errors (default 3).
        """
        if config is not None:
            self.config = config
        else:
            self.config = get_provider(provider)

        self.max_retries = max_retries if max_retries is not None else DEFAULT_MAX_RETRIES
        self._timeout = timeout or DEFAULT_TIMEOUT

        if self.config.sdk_type == 'anthropic':
            self._backend = _AnthropicBackend(self.config, self._timeout)
        else:
            self._backend = _OpenAIBackend(self.config, self._timeout)

    def chat_bench(self, messages, model=None, temperature=0.7, max_tokens=8192):
        """Bench 专用调用：返回 (content, reasoning_content)。

        max_tokens 默认 8192，给推理模型（deepseek-reasoner 等）足够空间完成思考后再输出正文。
        reasoning_content 仅 OpenAI-backend 且模型支持时非 None（如 deepseek-reasoner）。
        """
        model = model or self.config.default_model
        return self._backend.chat_raw(
            messages=messages, model=model,
            temperature=temperature, max_tokens=max_tokens,
        )

    def chat(self, messages, json_mode=False, model=None,
             temperature=0.7, max_tokens=1024):
        """Send a chat completion request.

        Returns the response content as a string.
        """
        model = model or self.config.default_model

        logger.debug(
            "LLM request: provider=%s model=%s messages=%d json_mode=%s",
            self.config.name, model, len(messages), json_mode,
        )

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                content = self._backend.chat(
                    messages=messages,
                    model=model,
                    json_mode=json_mode,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                logger.debug(
                    "LLM response: provider=%s model=%s",
                    self.config.name, model,
                )
                return content
            except Exception as e:
                if _is_transient(e):
                    last_error = e
                    if attempt < self.max_retries:
                        delay = min(BACKOFF_BASE * (2 ** (attempt - 1)), BACKOFF_CAP)
                        logger.warning(
                            "LLM request attempt %d/%d failed (%s), "
                            "retrying in %.1fs...",
                            attempt, self.max_retries, type(e).__name__, delay,
                        )
                        time.sleep(delay)
                    continue
                raise

        logger.warning(
            "LLM request failed after %d attempts (provider=%s error=%s: %s)",
            self.max_retries, self.config.name, type(last_error).__name__, last_error,
        )
        raise LLMRequestError(self.config.name, last_error)

    def chat_json(self, messages, model=None, temperature=0.7, max_tokens=1024):
        """Send a chat request and parse the response as JSON.

        Returns a dict.
        """
        content = self.chat(
            messages,
            json_mode=True,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if not content or not content.strip():
            _log_llm_parse_failure(messages, content or '', 'empty content')
            raise LLMJSONParseError(content or '', ValueError("LLM returned empty content"))

        # First parse attempt
        first_err: Exception
        try:
            return json.loads(_extract_json(content))
        except (json.JSONDecodeError, TypeError) as err:
            first_err = err

        # Repair attempt: show model its bad output, ask for pure JSON correction
        try:
            repair_messages = list(messages) + [
                {'role': 'assistant', 'content': content},
                {'role': 'user', 'content': '你的回复格式不对。请严格以JSON格式重新回复，不要有任何JSON之外的文字。'},
            ]
            repaired = self.chat(
                repair_messages, json_mode=True, model=model,
                temperature=0.2, max_tokens=max_tokens,
            )
            if repaired and repaired.strip():
                return json.loads(_extract_json(repaired))
        except Exception:
            pass

        # Both failed — log and raise
        _log_llm_parse_failure(messages, content, str(first_err))
        raise LLMJSONParseError(content, first_err)


def _extract_json(text):
    """从模型输出中健壮地提取第一个完整 JSON 对象。

    处理以下常见情况：
    - 纯 JSON
    - ```json ... ``` 代码块包装
    - JSON 前后有多余文字（如解释性说明）
    """
    if text is None:
        return ''
    text = text.strip()

    # 剥除 Markdown 代码块
    if text.startswith('```'):
        text = text[text.index('\n') + 1:] if '\n' in text else text[3:]
        if text.endswith('```'):
            text = text[:-3].rstrip()
        text = text.strip()

    # 定位第一个 { 或 [，忽略前置文字
    start = -1
    for i, ch in enumerate(text):
        if ch in ('{', '['):
            start = i
            break
    if start == -1:
        return text  # 找不到 JSON 起始符，原样返回供上层报错

    # raw_decode 在第一个合法 JSON 结束处停止，忽略后续多余内容
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, start)
        return json.dumps(obj)
    except json.JSONDecodeError:
        return text[start:]  # 退回到截断后的原文供上层报错


def _log_llm_parse_failure(messages, raw_content, reason):
    """JSON解析失败时记录原始提示词和响应，供问题分析使用。"""
    last_user = next(
        (m['content'] for m in reversed(messages) if m.get('role') == 'user'),
        '(no user message)',
    )
    logger.warning(
        "LLM JSON parse failed: %s\n"
        "--- RAW RESPONSE (%d chars) ---\n%s\n"
        "--- LAST USER PROMPT (%d chars) ---\n%s",
        reason,
        len(raw_content),
        raw_content[:800] if raw_content else '(empty)',
        len(last_user),
        last_user[:400],
    )


def _is_transient(exc):
    """Return True for errors that are worth retrying."""
    try:
        from openai import APIConnectionError, APITimeoutError, RateLimitError as OAIRateLimit
        if isinstance(exc, (APIConnectionError, APITimeoutError, OAIRateLimit)):
            return True
    except ImportError:
        pass
    try:
        import anthropic
        if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError,
                             anthropic.RateLimitError)):
            return True
    except ImportError:
        pass
    # 空响应/无文本块，值得重试
    if isinstance(exc, ValueError) and 'no text block' in str(exc):
        return True
    return False


class _OpenAIBackend:
    """OpenAI-SDK backend (supports any OpenAI-compatible endpoint)."""

    def __init__(self, config: ProviderConfig, timeout: float):
        self._config = config
        self._client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=timeout,
        )

    def chat_raw(self, messages, model, temperature, max_tokens):
        """返回 (content, reasoning_content) 元组。
        reasoning_content 仅 deepseek-reasoner 等推理模型会返回非 None 值。
        """
        response = self._client.chat.completions.create(
            model=model, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        msg = response.choices[0].message
        content = msg.content or ''
        reasoning = getattr(msg, 'reasoning_content', None) or ''
        return content, reasoning or None

    def chat(self, messages, model, json_mode, temperature, max_tokens):
        kwargs = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
        }
        if json_mode:
            kwargs['response_format'] = {'type': 'json_object'}
        response = self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ''


class _AnthropicBackend:
    """Anthropic-SDK backend (supports Anthropic API and compatible endpoints like MiniMax)."""

    def __init__(self, config: ProviderConfig, timeout: float):
        import anthropic
        self._config = config
        self._client = anthropic.Anthropic(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=timeout,
        )

    def chat_raw(self, messages, model, temperature, max_tokens):
        """返回 (content, None)，Anthropic backend 暂不暴露 thinking 块。"""
        return self.chat(messages, model, False, temperature, max_tokens), None

    def chat(self, messages, model, json_mode, temperature, max_tokens):
        # 将 OpenAI 格式的消息列表分离为 system 和 user/assistant 消息
        system_parts = []
        conv_messages = []
        for msg in messages:
            if msg['role'] == 'system':
                system_parts.append(msg['content'])
            else:
                conv_messages.append(msg)

        kwargs = {
            'model': model,
            'max_tokens': max_tokens,
            'messages': conv_messages,
            'temperature': temperature,
        }
        if system_parts:
            kwargs['system'] = '\n\n'.join(system_parts)

        response = self._client.messages.create(**kwargs)
        # 提取 text 块（thinking 块是内部推理过程，不作为响应内容）
        for block in response.content:
            if getattr(block, 'type', None) == 'text':
                return block.text
        # 未找到 text 块（可能是纯 thinking 响应或空响应），抛出可重试错误
        raise ValueError(f"LLM returned no text block (blocks={[getattr(b,'type',None) for b in response.content]})")
