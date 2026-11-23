"""Trusted service configuration. No privileged values are returned to clients."""
from dataclasses import dataclass
import os
import shutil


@dataclass(frozen=True)
class Settings:
    database_url: str = ''
    allowed_origins: tuple[str, ...] = ('https://haroldng.dev', 'http://localhost:4321', 'http://127.0.0.1:4321')
    supabase_url: str = ''
    supabase_key: str = ''
    storage_bucket: str = 'research-artifacts'
    artifact_root: str = ''
    capacity_path: str = ''
    min_free_bytes: int = 1073741824
    session_secret: str = ''
    pool_size: int = 8
    max_queued: int = 100
    max_active_session: int = 3
    max_requests_session: int = 12
    max_requests_network: int = 30
    max_seconds: int = 120
    max_results: int = 1000000
    result_retention_days: int = 30
    max_attempts: int = 3
    worker_concurrency: int = 2

    @classmethod
    def from_env(cls):
        values = {}
        for field in cls.__dataclass_fields__.values():
            name = 'RESEARCH_' + field.name.upper()
            if name not in os.environ:
                continue
            value = os.environ[name]
            if field.name == 'allowed_origins':
                value = tuple(part.strip() for part in value.split(',') if part.strip())
            elif isinstance(field.default, int):
                value = int(value)
                if value < 1:
                    raise ValueError(name + ' must be positive')
            values[field.name] = value
        return cls(**values)


class CapacityUnavailable(ValueError):
    pass


def check_capacity(settings):
    if not settings.capacity_path:
        return
    try:
        available = shutil.disk_usage(settings.capacity_path).free
    except OSError:
        raise CapacityUnavailable('Search capacity cannot be checked. Browsing remains available; retry shortly.') from None
    if available < settings.min_free_bytes:
        raise CapacityUnavailable('Team search is at disk capacity. Browsing remains available; retry shortly.')
