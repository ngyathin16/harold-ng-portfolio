"""Private Cloud Run service; invoke only through platform IAM authentication."""
from contextlib import asynccontextmanager, contextmanager
import os
import threading

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .cloud import CloudRun
from .config import Settings
from .dispatch import dispatch_once
from .markers import GCSMarkers, cleanup


@contextmanager
def dispatch_deadline(seconds=150):
    """Stop this bounded process if synchronous request work cannot return."""
    finished = threading.Event()
    def stop():
        if not finished.wait(seconds):
            os._exit(124)
    threading.Thread(target=stop, name='dispatch-deadline', daemon=True).start()
    try:
        yield
    finally:
        finished.set()


def configure_connection(connection):
    connection.execute("SET statement_timeout='5s'; SET lock_timeout='3s'")
    connection.commit()


class Wakeup(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str | None = Field(default=None, min_length=1, max_length=160)
    generation: int | None = Field(default=None, ge=1)


def create_app(settings=None, pool=None, cloud=None, markers=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        if not os.environ.get('K_SERVICE') and pool is None:
            raise RuntimeError('Dispatcher requires private Cloud Run IAM ingress; local tests must inject dependencies')
        try:
            if settings.budget_window_id:
                from .budget import Budget
                app.state.budget_pool = ConnectionPool(settings.database_url, min_size=0, max_size=1, timeout=3,
                    kwargs={'row_factory': dict_row, 'connect_timeout': 5},
                    configure=configure_connection, check=ConnectionPool.check_connection)
                app.state.budget = Budget(app.state.budget_pool, settings.budget_window_id)
            if app.state.markers is None:
                app.state.markers = GCSMarkers(settings)
            if app.state.cloud is None:
                charge = (lambda: app.state.budget.charge(cloud_control_calls=1, google_egress_bytes=131072)) if app.state.budget else None
                app.state.cloud = CloudRun(settings, charge=charge)
            if app.state.pool is None:
                app.state.pool = ConnectionPool(settings.database_url, min_size=0, max_size=1 if settings.budget_window_id else 2, timeout=3,
                    kwargs={'row_factory': dict_row, 'connect_timeout': 5},
                    configure=configure_connection, check=ConnectionPool.check_connection)
            yield
        finally:
            if pool is None and app.state.pool is not None:
                app.state.pool.close()
            if cloud is None and app.state.cloud is not None:
                app.state.cloud.close()
            if markers is None and app.state.markers is not None:
                app.state.markers.close()
            if app.state.budget_pool is not None:
                app.state.budget_pool.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.pool = pool
    app.state.cloud = cloud
    app.state.markers = markers
    app.state.budget_pool = None
    app.state.budget = None

    @app.get('/health/live')
    def live():
        return {'status': 'ok'}

    @app.post('/dispatch')
    def dispatch(wakeup: Wakeup):
        with dispatch_deadline():
            # The payload is a wakeup hint, never authority to choose a job or command.
            try:
                listed = app.state.markers.list()
                if not listed:
                    return {'launches': 0, 'idle': True}
                if app.state.budget is not None:
                    # Empty scheduled listings are prepaid in the finite review.
                    app.state.budget.invocation()
                    app.state.budget.charge(gcs_class_a=17, google_egress_bytes=17 * 8192)
                result = dispatch_once(app.state.pool, settings, app.state.cloud)
                cleanup(app.state.pool, app.state.markers, listed)
                if result.get('retry_pending'):
                    raise HTTPException(503, 'Launch is being reconciled; retry this wakeup.')
                return result
            except Exception:
                raise HTTPException(503, 'Dispatch unavailable; durable work remains queued.') from None

    return app


app = create_app()
