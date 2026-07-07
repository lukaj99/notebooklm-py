"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """API settings loaded from environment variables.

    All settings have sensible defaults. Override with env vars prefixed NOTEBOOKLM_API_:
        NOTEBOOKLM_API_HOST=0.0.0.0
        NOTEBOOKLM_API_PORT=8000
        NOTEBOOKLM_API_STORAGE_PATH=~/.notebooklm/storage_state.json
    """

    host: str = "127.0.0.1"
    port: int = 8000
    storage_path: str | None = None
    request_timeout: float = 30.0
    cors_origins: list[str] = ["*"]
    log_level: str = "info"
    # Shared secret required (via the X-API-Key header) on every /api/v1
    # route except /health. Unset means the API fails closed — every
    # request is rejected with 503 rather than silently served with no
    # authentication at all. See deps.require_api_key.
    api_key: str | None = None

    model_config = {"env_prefix": "NOTEBOOKLM_API_"}


settings = Settings()
