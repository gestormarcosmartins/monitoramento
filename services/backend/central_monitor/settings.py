from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    central_database_url: str = Field(alias="CENTRAL_DATABASE_URL")
    redis_url: str = Field(alias="REDIS_URL")
    central_secret_key: str = Field(min_length=32, alias="CENTRAL_SECRET_KEY")
    zabbix_api_url: str = Field(alias="ZABBIX_API_URL")
    zabbix_api_token: str = Field(default="", alias="ZABBIX_API_TOKEN")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


@lru_cache
def get_settings() -> Settings:
    return Settings()

