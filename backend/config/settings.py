import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.getenv('DEBUG', 'True').lower() in ('true', '1', 'yes')

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY') or ('dev-secret-key-change-in-production' if DEBUG else None)
if not SECRET_KEY:
    raise RuntimeError(
        'DJANGO_SECRET_KEY must be set when DEBUG is False. '
        'Refusing to start with the development fallback key.'
    )

_allowed_hosts_env = os.getenv('ALLOWED_HOSTS', '').strip()
if _allowed_hosts_env:
    ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts_env.split(',') if h.strip()]
elif DEBUG:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1', '[::1]', '0.0.0.0', 'backend']
else:
    raise RuntimeError(
        'ALLOWED_HOSTS must be set (comma-separated) when DEBUG is False.'
    )

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'rest_framework',
    'corsheaders',
    # Local
    'game.apps.GameConfig',
    'llm',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database - PostgreSQL
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('POSTGRES_DB', 'mandarin_game'),
        'USER': os.getenv('POSTGRES_USER', 'postgres'),
        'PASSWORD': os.getenv('POSTGRES_PASSWORD', 'postgres'),
        'HOST': os.getenv('POSTGRES_HOST', 'postgres'),
        'PORT': os.getenv('POSTGRES_PORT', '5432'),
    }
}

# Cache - Redis
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': os.getenv('REDIS_URL', 'redis://redis:6379/0'),
    }
}

# Celery
CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = 'django-db'

# DRF - Session authentication (C版本极简)
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
}

# CORS
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOW_CREDENTIALS = True

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
]

# Internationalization
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Reverse proxy: when deployed behind Nginx that strips a path prefix (e.g. /g1),
# set FORCE_SCRIPT_NAME=/g1 via env so Django generates correct URLs (admin, static, etc.).
# Leave unset for local Docker dev (app runs at /).
_script_name = os.getenv('FORCE_SCRIPT_NAME', '')
if _script_name:
    FORCE_SCRIPT_NAME = _script_name

# AI Negotiation — set True to enable LLM-driven negotiation for neighbor counties
# (adds ~2 LLM calls per annexation/hidden-land event; keep False when many AI counties run)
AI_NEGOTIATION_ENABLED = os.getenv('AI_NEGOTIATION_ENABLED', '').lower() in ('true', '1', 'yes')
JUDICIAL_MAGISTRATE_LLM_ENABLED = os.getenv('JUDICIAL_MAGISTRATE_LLM_ENABLED', 'true').lower() in ('true', '1', 'yes')
JUDICIAL_MAGISTRATE_LLM_TIMEOUT = float(os.getenv('JUDICIAL_MAGISTRATE_LLM_TIMEOUT', '8'))
JUDICIAL_MAGISTRATE_LLM_MAX_RETRIES = int(os.getenv('JUDICIAL_MAGISTRATE_LLM_MAX_RETRIES', '1'))

# LLM Providers
LLM_DEFAULT_PROVIDER = os.getenv('LLM_DEFAULT_PROVIDER', 'qwen')

LLM_PROVIDERS = {
    'openai': {
        'base_url': 'https://api.openai.com/v1',
        'api_key': os.getenv('OPENAI_API_KEY', ''),
        'default_model': os.getenv('OPENAI_MODEL', 'gpt-4o'),
    },
    'qwen': {
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'api_key': os.getenv('QWEN_API_KEY', ''),
        'default_model': os.getenv('QWEN_MODEL', 'qwen-plus'),
    },
    'deepseek': {
        'base_url': 'https://api.deepseek.com/v1',
        'api_key': os.getenv('DEEPSEEK_API_KEY', ''),
        'default_model': os.getenv('DEEPSEEK_MODEL', 'deepseek-chat'),
    },
    'minimax': {
        'sdk_type': 'anthropic',
        'base_url': os.getenv('ANTHROPIC_BASE_URL', 'https://api.minimax.io/anthropic'),
        'api_key': os.getenv('ANTHROPIC_API_KEY', ''),
        'default_model': os.getenv('MINIMAX_MODEL', 'MiniMax-M2.1'),
    },
}

# Feishu log webhook — set in .env to enable WARNING+ push notifications
FEISHU_LOG_WEBHOOK = os.getenv('FEISHU_LOG_WEBHOOK', '')
FEISHU_FEEDBACK_WEBHOOK = os.getenv('FEISHU_FEEDBACK_WEBHOOK', FEISHU_LOG_WEBHOOK)

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {name} {message}',
            'style': '{',
        },
        'feishu': {
            'format': '[{levelname}] {name}\n{message}\n→ {pathname}:{lineno}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'feishu': {
            'class': 'config.logging_handlers.FeishuHandler',
            'level': 'WARNING',
            'formatter': 'feishu',
            'webhook_url': FEISHU_LOG_WEBHOOK,
        },
    },
    'loggers': {
        'llm': {
            'handlers': ['console', 'feishu'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'game': {
            'handlers': ['console', 'feishu'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
    },
}
