"""Application configuration via pydantic-settings."""

import secrets
import warnings

from pydantic_settings import BaseSettings

_INSECURE_DEFAULT = "change-me-in-production"


class Settings(BaseSettings):
    """Server configuration loaded from environment variables."""

    DATABASE_URL: str = "postgresql+asyncpg://handoff:handoff@localhost:5432/handoff"
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET: str = _INSECURE_DEFAULT
    JWT_EXPIRY_HOURS: int = 24
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000
    CORS_ORIGINS: str = "http://localhost:3000"
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"  # development | staging | production

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()

# Block production startup with the default JWT secret
if settings.JWT_SECRET == _INSECURE_DEFAULT:
    if settings.ENVIRONMENT == "production":
        raise RuntimeError(
            "FATAL: JWT_SECRET is set to the default value. "
            "Set a strong secret via the JWT_SECRET environment variable before running in production."
        )
    else:
        # In development, warn loudly and generate a random secret so tests work
        warnings.warn(
            "JWT_SECRET is using the insecure default — auto-generating a random secret for this session. "
            "Set JWT_SECRET in your .env for stable tokens across restarts.",
            stacklevel=2,
        )
        settings.JWT_SECRET = secrets.token_urlsafe(48)
