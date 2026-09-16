from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ai-infra-platform-api"
    app_env: str = "local"
    app_version: str = "0.1.0"
    log_level: str = "INFO"

    server_host: str = "127.0.0.1"
    server_port: int = 8000
    server_root_path: str = ""
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]

    database_dsn: str = "postgresql+asyncpg://app:app@127.0.0.1:5432/ai_infra"
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_recycle_seconds: int = 1800
    db_statement_timeout_ms: int = 5000
    db_echo: bool = False

    redis_url: str = "redis://127.0.0.1:6379/0"
    cache_ttl_seconds: int = 30

    secret_pepper: str = ""
    api_key_prefix_length: int = 8
    auth_required: bool = False

    default_page_size: int = 50
    max_page_size: int = 200

    expected_schema_version: int = 1

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
