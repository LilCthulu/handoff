"""Application configuration via pydantic-settings.

All configuration comes from environment variables. In development,
values are loaded from .env files with safe defaults. In production,
required variables must be set explicitly — the server refuses to
start with insecure defaults.
"""

import secrets
import warnings

from pydantic_settings import BaseSettings

_INSECURE_DEFAULT = "change-me-in-production"


class Settings(BaseSettings):
    """Server configuration loaded from environment variables."""

    # Core
    DATABASE_URL: str = "postgresql+asyncpg://handoff:handoff@localhost:5432/handoff"
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET: str = _INSECURE_DEFAULT
    JWT_EXPIRY_HOURS: int = 24
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000
    CORS_ORIGINS: str = "http://localhost:3000"
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"  # development | staging | production

    # Cloud extension (optional — set when running with handoff-cloud)
    PII_ENCRYPTION_KEY: str = ""
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_STARTER: str = ""
    STRIPE_PRICE_TEAM: str = ""
    STRIPE_PRICE_ENTERPRISE: str = ""
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    OAUTH_REDIRECT_BASE: str = "http://localhost:3000"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()


def _validate_production() -> None:
    """Fail fast if production is missing required configuration."""
    errors: list[str] = []

    if settings.JWT_SECRET == _INSECURE_DEFAULT:
        errors.append("JWT_SECRET must be set to a strong secret")

    if "localhost" in settings.DATABASE_URL or "sqlite" in settings.DATABASE_URL:
        errors.append("DATABASE_URL must point to a production PostgreSQL instance")

    if settings.CORS_ORIGINS == "http://localhost:3000":
        errors.append("CORS_ORIGINS must be set to your production domain(s)")

    if errors:
        msg = "FATAL: Production environment validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        raise RuntimeError(msg)


def _validate_development() -> None:
    """In development, warn about insecure defaults and auto-fix JWT."""
    if settings.JWT_SECRET == _INSECURE_DEFAULT:
        warnings.warn(
            "JWT_SECRET is using the insecure default — auto-generating a random secret for this session. "
            "Set JWT_SECRET in your .env for stable tokens across restarts.",
            stacklevel=3,
        )
        settings.JWT_SECRET = secrets.token_urlsafe(48)


# Run validation on import
if settings.ENVIRONMENT == "production":
    _validate_production()
else:
    _validate_development()
