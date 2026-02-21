import json
import logging
import time

from openai import OpenAI, APIConnectionError, APITimeoutError, RateLimitError

from .exceptions import LLMJSONParseError, LLMRequestError
from .providers import ProviderConfig, get_provider

logger = logging.getLogger('llm')

DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 60.0
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
        self._client = OpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            timeout=timeout or DEFAULT_TIMEOUT,
        )

    def chat(self, messages, json_mode=False, model=None,
             temperature=0.7, max_tokens=1024):
        """Send a chat completion request.

        Returns the response content as a string.
        """
        model = model or self.config.default_model
        kwargs = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
        }
        if json_mode:
            kwargs['response_format'] = {'type': 'json_object'}

        logger.debug(
            "LLM request: provider=%s model=%s messages=%d json_mode=%s",
            self.config.name, model, len(messages), json_mode,
        )

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
                logger.debug(
                    "LLM response: provider=%s model=%s tokens=%s",
                    self.config.name, model,
                    getattr(response.usage, 'total_tokens', 'N/A'),
                )
                return content
            except (APIConnectionError, APITimeoutError, RateLimitError) as e:
                last_error = e
                if attempt < self.max_retries:
                    delay = min(BACKOFF_BASE * (2 ** (attempt - 1)), BACKOFF_CAP)
                    logger.warning(
                        "LLM request attempt %d/%d failed (%s), "
                        "retrying in %.1fs...",
                        attempt, self.max_retries, type(e).__name__, delay,
                    )
                    time.sleep(delay)

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
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError) as e:
            raise LLMJSONParseError(content, e)
