"""Pytest settings.

Use lightweight local backends to avoid requiring Postgres/Redis in unit tests.
"""

from .settings import *  # noqa: F401,F403


# Shared in-memory sqlite (file URI w/ shared cache) so background threads
# spawned during tests see the same tables as the main test thread.
# Plain ":memory:" gives each connection its own private DB, causing
# "no such table" / "database table is locked" races in threaded code paths
# (e.g. neighbor ThreadPoolExecutor, judicial background generation).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "file:governor_test?mode=memory&cache=shared",
        "OPTIONS": {
            "uri": True,
            "timeout": 30,
        },
        "TEST": {
            "NAME": "file:governor_test?mode=memory&cache=shared",
        },
    }
}


# Local memory cache for tests (no Redis dependency required).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cache",
    }
}


# Make password hashing fast in tests.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]


# Disable real LLM calls in tests, even if local env provides API keys.
LLM_PROVIDERS = {
    name: {**cfg, "api_key": ""}
    for name, cfg in LLM_PROVIDERS.items()
}
