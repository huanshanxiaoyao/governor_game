from dataclasses import dataclass

from django.conf import settings

from .exceptions import LLMProviderNotFound


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    default_model: str


def get_provider(name=None):
    """Load a provider config from settings.LLM_PROVIDERS.

    Falls back to settings.LLM_DEFAULT_PROVIDER when name is None.
    """
    if name is None:
        name = getattr(settings, 'LLM_DEFAULT_PROVIDER', 'openai')

    providers = getattr(settings, 'LLM_PROVIDERS', {})
    if name not in providers:
        raise LLMProviderNotFound(name)

    cfg = providers[name]
    return ProviderConfig(
        name=name,
        base_url=cfg['base_url'],
        api_key=cfg['api_key'],
        default_model=cfg['default_model'],
    )


def get_all_providers():
    """Return a dict of all configured providers."""
    providers = getattr(settings, 'LLM_PROVIDERS', {})
    return {
        name: ProviderConfig(
            name=name,
            base_url=cfg['base_url'],
            api_key=cfg['api_key'],
            default_model=cfg['default_model'],
        )
        for name, cfg in providers.items()
    }
