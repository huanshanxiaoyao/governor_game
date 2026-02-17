import json
import logging
import time

from openai import OpenAI, APIConnectionError, APITimeoutError, RateLimitError

from .exceptions import LLMJSONParseError, LLMRequestError
from .providers import ProviderConfig, get_provider

logger = logging.getLogger('llm')

MAX_RETRIES = 3
BACKOFF_BASE = 1  # seconds
BACKOFF_CAP = 30  # seconds


class LLMClient:
    """Unified LLM client that works with any OpenAI-compatible provider."""

    def __init__(self, provider=None, config=None):
        """Initialize client.

        Args:
            provider: Provider name string (loaded from settings).
            config: ProviderConfig instance (takes precedence over provider).
        """
        if config is not None:
            self.config = config
        else:
            self.config = get_provider(provider)

        self._client = OpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            timeout=60.0,
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
        for attempt in range(1, MAX_RETRIES + 1):
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
                if attempt < MAX_RETRIES:
                    delay = min(BACKOFF_BASE * (2 ** (attempt - 1)), BACKOFF_CAP)
                    logger.warning(
                        "LLM request attempt %d/%d failed (%s), "
                        "retrying in %.1fs...",
                        attempt, MAX_RETRIES, type(e).__name__, delay,
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
