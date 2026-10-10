from dataclasses import asdict, dataclass

import psycopg
import redis

from .settings import get_settings


@dataclass(frozen=True)
class DependencyStatus:
    postgres: str
    redis: str

    @property
    def ready(self) -> bool:
        return self.postgres == "up" and self.redis == "up"

    def as_dict(self) -> dict[str, str | bool]:
        return {**asdict(self), "ready": self.ready}


def check_dependencies() -> DependencyStatus:
    settings = get_settings()
    postgres_status = "down"
    redis_status = "down"

    try:
        with psycopg.connect(settings.central_database_url, connect_timeout=3) as connection:
            connection.execute("SELECT 1")
        postgres_status = "up"
    except psycopg.Error:
        pass

    try:
        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=3)
        if client.ping():
            redis_status = "up"
    except redis.RedisError:
        pass

    return DependencyStatus(postgres=postgres_status, redis=redis_status)

