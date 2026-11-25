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
    deployment_mode: str = 'vm'
    client_ip_mode: str = 'peer'
    max_outstanding: int = 2
    max_launches_per_minute: int = 4
    max_launch_attempts: int = 3
    dispatch_batch_size: int = 2
    dispatch_job: str = ''
    task_queue: str = ''
    dispatcher_url: str = ''
    task_service_account: str = ''
    dispatch_marker_bucket: str = ''
    dispatch_marker_prefix: str = 'dispatch/'
    capacity_max_age_seconds: int = 900
    capacity_measurement_mode: str = 'manual'
    capacity_limit_bytes: int = 1073741824
    result_reservation_bytes: int = 2147483648
    database_headroom_bytes: int = 1073741824
    result_storage_mode: str = 'postgres'
    result_credentials_json: str = ''
    result_bucket: str = ''
    result_prefix: str = 'results/'
    result_local_root: str = ''
    result_scratch_root: str = ''
    result_scratch_bytes: int = 536870912
    budget_window_id: str = ''

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
        settings = cls(**values)
        if settings.deployment_mode not in ('vm', 'serverless'):
            raise ValueError('Invalid deployment mode')
        if settings.client_ip_mode not in ('peer', 'shared'):
            raise ValueError('Invalid client IP mode')
        if settings.result_storage_mode not in ('postgres', 'objects'):
            raise ValueError('Invalid result storage mode')
        if settings.capacity_measurement_mode not in ('manual', 'neon_branch'):
            raise ValueError('Invalid capacity measurement mode')
        if settings.capacity_measurement_mode == 'neon_branch' and settings.capacity_limit_bytes > 1073741824:
            raise ValueError('Neon branch capacity exceeds the qualified limit')
        if settings.result_storage_mode == 'objects' and not (settings.result_credentials_json or settings.result_local_root):
            raise ValueError('Object result storage requires restricted credentials or a local root')
        if settings.deployment_mode == 'serverless':
            if settings.result_storage_mode == 'objects' and not settings.budget_window_id:
                raise ValueError('Serverless result storage requires an explicitly reviewed resource allowance')
            from psycopg.conninfo import conninfo_to_dict
            if settings.database_url and conninfo_to_dict(settings.database_url).get('sslmode') != 'verify-full':
                raise ValueError('Serverless database connections require sslmode=verify-full')
            if settings.client_ip_mode != 'shared':
                raise ValueError('Serverless requires shared network quotas until ingress is qualified')
        return settings


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
