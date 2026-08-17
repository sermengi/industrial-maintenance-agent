from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Industrial Maintenance Agent"
    app_version: str = "0.1.0"
    app_env: str = Field(default="local", validation_alias="APP_ENV")
    api_host: str = Field(default="127.0.0.1", validation_alias="API_HOST")
    api_port: int = Field(default=8000, validation_alias="API_PORT")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    test_database_url: str | None = Field(default=None, validation_alias="TEST_DATABASE_URL")
    voyage_api_key: str | None = Field(default=None, validation_alias="VOYAGE_API_KEY")
    rag_embedding_backend: str = Field(default="voyage", validation_alias="RAG_EMBEDDING_BACKEND")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def debug(self) -> bool:
        return self.app_env.lower() in {"local", "development", "dev"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
