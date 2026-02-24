"""Pytest settings.

Use lightweight local backends to avoid requiring Postgres/Redis in unit tests.
"""

from .settings import *  # noqa: F401,F403


# In-memory sqlite for tests (no psycopg dependency required).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
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
