"""Private Cloud Run service; invoke only through platform IAM authentication."""
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .cloud import CloudRun
from .config import Settings
from .dispatch import dispatch_once


class Wakeup(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str | None = Field(default=None, min_length=1, max_length=160)
    generation: int | None = Field(default=None, ge=1)


def create_app(settings=None, pool=None, cloud=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        if not os.environ.get('K_SERVICE') and pool is None:
            raise RuntimeError('Dispatcher requires private Cloud Run IAM ingress; local tests must inject dependencies')
        if app.state.pool is None:
            app.state.pool = ConnectionPool(settings.database_url, min_size=0, max_size=2, timeout=3,
                kwargs={'row_factory': dict_row})
        if app.state.cloud is None:
            app.state.cloud = CloudRun(settings)
        yield
        if pool is None:
            app.state.pool.close()
        if cloud is None:
            app.state.cloud.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.pool = pool
    app.state.cloud = cloud

    @app.get('/health/live')
    def live():
        return {'status': 'ok'}

    @app.post('/dispatch')
    def dispatch(wakeup: Wakeup):
        # The payload is a wakeup hint, never authority to choose a job or command.
        try:
            return dispatch_once(app.state.pool, settings, app.state.cloud)
        except Exception:
            raise HTTPException(503, 'Dispatch unavailable; durable work remains queued.') from None

    return app


app = create_app()
