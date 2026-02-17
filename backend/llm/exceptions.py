class LLMError(Exception):
    """Base exception for LLM module."""


class LLMProviderNotFound(LLMError):
    """Raised when a provider name is not found in settings."""

    def __init__(self, provider_name):
        self.provider_name = provider_name
        super().__init__(f"LLM provider not found: '{provider_name}'")


class LLMRequestError(LLMError):
    """Raised when an API call fails after all retries."""

    def __init__(self, provider, original_error):
        self.provider = provider
        self.original_error = original_error
        super().__init__(
            f"LLM request failed for provider '{provider}': {original_error}"
        )


class LLMJSONParseError(LLMError):
    """Raised when the LLM response cannot be parsed as JSON."""

    def __init__(self, raw_content, parse_error=None):
        self.raw_content = raw_content
        self.parse_error = parse_error
        super().__init__(
            f"Failed to parse LLM response as JSON: {parse_error}"
        )
