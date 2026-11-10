"""
Django settings for fuelroute project.
Production-ready configuration supporting environment overrides via django-environ.
"""

from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, True),
    SECRET_KEY=(str, "django-insecure-fuelroute-production-ready-assessment-secret-key-2026"),
    ALLOWED_HOSTS=(list, ["*"]),
    OSRM_BASE_URL=(str, "https://router.project-osrm.org"),
    ROUTING_TIMEOUT_SECONDS=(float, 10.0),
    ROUTING_MAX_RETRIES=(int, 2),
    MAX_DETOUR_MILES=(float, 5.0),
    DETOUR_PENALTY_ENABLED=(bool, True),
    DEFAULT_MPG=(float, 10.0),
    TANK_CAPACITY_GALLONS=(float, 50.0),
    NOMINATIM_FALLBACK_ENABLED=(bool, False),
    CACHE_TTL=(int, 86400),
)

# Read .env file if it exists
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "drf_spectacular",
    # Local application
    "routing.apps.RoutingConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "fuelroute.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "routing" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "fuelroute.wsgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}

CACHES = {
    "default": env.cache("CACHE_URL", default="locmemcache://fuelroute-cache")
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    # Throttling off by default
}

# DRF Spectacular OpenAPI documentation
SPECTACULAR_SETTINGS = {
    "TITLE": "FuelRoute API",
    "DESCRIPTION": (
        "Optimal fuel stop planner and driving route optimizer across the USA. "
        "Finds the cheapest fuel stops for long-distance trips respecting a 500-mile vehicle range."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# Structured Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {
            "format": "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "verbose": {
            "format": "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)d] %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "structured",
        },
    },
    "loggers": {
        "routing": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

# Domain-specific settings
OSRM_BASE_URL = env("OSRM_BASE_URL").rstrip("/")
ROUTING_TIMEOUT_SECONDS = env("ROUTING_TIMEOUT_SECONDS")
ROUTING_MAX_RETRIES = env("ROUTING_MAX_RETRIES")
MAX_DETOUR_MILES = env("MAX_DETOUR_MILES")
DETOUR_PENALTY_ENABLED = env("DETOUR_PENALTY_ENABLED")
DEFAULT_MPG = env("DEFAULT_MPG")
TANK_CAPACITY_GALLONS = env("TANK_CAPACITY_GALLONS")
NOMINATIM_FALLBACK_ENABLED = env("NOMINATIM_FALLBACK_ENABLED")
CACHE_TTL = env("CACHE_TTL")
DATA_DIR = BASE_DIR / "data"
