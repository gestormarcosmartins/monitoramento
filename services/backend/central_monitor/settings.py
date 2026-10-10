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
    glpi_api_url: str = Field(default="http://glpi/apirest.php", alias="GLPI_API_URL")
    glpi_app_token: str = Field(default="", alias="GLPI_APP_TOKEN")
    glpi_user_token: str = Field(default="", alias="GLPI_USER_TOKEN")
    glpi_username: str = Field(default="", alias="GLPI_USERNAME")
    glpi_password: str = Field(default="", alias="GLPI_PASSWORD")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    oidc_issuer: str = Field(default="", alias="OIDC_ISSUER")
    oidc_jwks_url: str = Field(default="", alias="OIDC_JWKS_URL")
    oidc_audience: str = Field(default="central-monitor", alias="OIDC_AUDIENCE")
    dev_auth_bypass: bool = Field(default=False, alias="DEV_AUTH_BYPASS")
    monitor_interval_seconds: int = Field(default=30, ge=10, alias="MONITOR_INTERVAL_SECONDS")
    action_execution_enabled: bool = Field(default=False, alias="ACTION_EXECUTION_ENABLED")


@lru_cache
def get_settings() -> Settings:
    return Settings()

