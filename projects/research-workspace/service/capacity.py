"""On-demand logical branch measurements for the qualified Neon PostgreSQL 17 path."""
import psycopg

from .config import CapacityUnavailable


def refresh(conn, settings):
    if settings.capacity_measurement_mode == 'manual':
        return
    if (settings.capacity_measurement_mode != 'neon_branch'
            or type(settings.capacity_limit_bytes) is not int
            or not 1 <= settings.capacity_limit_bytes <= 1073741824):
        raise CapacityUnavailable('Managed database capacity measurement is not configured.')
    try:
        conn.execute('SELECT research.refresh_neon_capacity(%s)', (settings.capacity_limit_bytes,))
    except psycopg.Error:
        raise CapacityUnavailable('Search capacity cannot be measured. Browsing remains available; retry later.') from None
