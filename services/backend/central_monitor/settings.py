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
    zabbix_username: str = Field(default="Admin", alias="ZABBIX_USERNAME")
    zabbix_password: str = Field(default="zabbix", alias="ZABBIX_PASSWORD")
    glpi_api_url: str = Field(default="http://glpi/apirest.php", alias="GLPI_API_URL")
    glpi_app_token: str = Field(default="", alias="GLPI_APP_TOKEN")
    glpi_user_token: str = Field(default="", alias="GLPI_USER_TOKEN")
    glpi_username: str = Field(default="", alias="GLPI_USERNAME")
    glpi_password: str = Field(default="", alias="GLPI_PASSWORD")
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_username: str = Field(default="", alias="SMTP_USERNAME")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="noreply@nexora.local", alias="SMTP_FROM")
    smtp_tls: bool = Field(default=True, alias="SMTP_TLS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    oidc_issuer: str = Field(default="", alias="OIDC_ISSUER")
    oidc_jwks_url: str = Field(default="", alias="OIDC_JWKS_URL")
    oidc_audience: str = Field(default="central-monitor", alias="OIDC_AUDIENCE")
    dev_auth_bypass: bool = Field(default=False, alias="DEV_AUTH_BYPASS")
    monitor_interval_seconds: int = Field(default=30, ge=10, alias="MONITOR_INTERVAL_SECONDS")
    action_execution_enabled: bool = Field(default=False, alias="ACTION_EXECUTION_ENABLED")
    benner_ingest_token: str = Field(default="", alias="BENNER_INGEST_TOKEN")


@lru_cache
def get_settings() -> Settings:
    return Settings()

