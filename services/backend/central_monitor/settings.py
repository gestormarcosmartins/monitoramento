from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    central_public_url: str = Field(default="http://localhost:8090", alias="CENTRAL_PUBLIC_URL")
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    central_database_url: str = Field(alias="CENTRAL_DATABASE_URL")
    database_pool_min_size: int = Field(default=2, ge=1, alias="DATABASE_POOL_MIN_SIZE")
    database_pool_max_size: int = Field(default=12, ge=1, alias="DATABASE_POOL_MAX_SIZE")
    database_pool_timeout_seconds: float = Field(default=5, gt=0, alias="DATABASE_POOL_TIMEOUT_SECONDS")
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
    oidc_extra_clients: str = Field(default="nexora-mobile", alias="OIDC_EXTRA_CLIENTS")
    keycloak_base_url: str = Field(default="http://keycloak:8080/auth", alias="KEYCLOAK_BASE_URL")
    keycloak_realm: str = Field(default="central-monitor", alias="KEYCLOAK_REALM")
    keycloak_admin_username: str = Field(default="", alias="KEYCLOAK_ADMIN_USERNAME")
    keycloak_admin_password: str = Field(default="", alias="KEYCLOAK_ADMIN_PASSWORD")
    dev_auth_bypass: bool = Field(default=False, alias="DEV_AUTH_BYPASS")
    monitor_push_failure_grace_seconds: int = Field(default=600, ge=0, le=86400, alias="MONITOR_PUSH_FAILURE_GRACE_SECONDS")
    monitor_interval_seconds: int = Field(default=30, ge=10, alias="MONITOR_INTERVAL_SECONDS")
    action_execution_enabled: bool = Field(default=False, alias="ACTION_EXECUTION_ENABLED")
    expo_access_token: str = Field(default="", alias="EXPO_ACCESS_TOKEN")
    expo_project_ids: str = Field(default="", alias="EXPO_PROJECT_IDS")
    vapid_public_key: str = Field(default="", alias="VAPID_PUBLIC_KEY")
    vapid_private_key: str = Field(default="", alias="VAPID_PRIVATE_KEY")
    vapid_subject: str = Field(default="mailto:noreply@nexora.local", alias="VAPID_SUBJECT")
    benner_ingest_token: str = Field(default="", alias="BENNER_INGEST_TOKEN")
    # Ed25519 seed (base64, 32 bytes) used to sign published Business query revisions.
    business_signing_key: str = Field(default="", alias="BUSINESS_SIGNING_KEY")
    # Directory with Business Worker release packages (copied there by operations); registering a release hashes the file.
    business_releases_dir: str = Field(default="/data/releases", alias="BUSINESS_RELEASES_DIR")
    # RabbitMQ for Business job dispatch (central only; workers never see it). Empty = "db" mode.
    rabbitmq_url: str = Field(default="", alias="RABBITMQ_URL")
    rabbitmq_queue_type: str = Field(default="classic", alias="RABBITMQ_QUEUE_TYPE")
    expo_receipt_delay_seconds: int = Field(default=900, ge=60, alias="EXPO_RECEIPT_DELAY_SECONDS")


@lru_cache
def get_settings() -> Settings:
    return Settings()

