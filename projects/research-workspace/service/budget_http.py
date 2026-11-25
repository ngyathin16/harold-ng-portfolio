"""Commit HTTP allowances before work and before releasing a buffered response.

Denied responses, infrastructure probes, disconnects, and platform-generated
traffic cannot be metered recursively here. These counters are application
allowances, not a hard provider invoice cap.
"""
from __future__ import annotations

import json

import psycopg
from psycopg_pool import PoolTimeout
from starlette.concurrency import run_in_threadpool

from .budget import BudgetUnavailable


MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_UNAVAILABLE = (BudgetUnavailable, PoolTimeout, psycopg.OperationalError)
_DETAIL = 'The reviewed resource allowance is unavailable or exhausted. Retry after operator review.'


class _ResponseTooLarge(Exception):
    pass


class BudgetHTTPMiddleware:
    def __init__(self, app, *, state, allow_origins=(), max_response_bytes=MAX_RESPONSE_BYTES, required=False):
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= MAX_RESPONSE_BYTES:
            raise ValueError('HTTP response allowance must be between 1 byte and 8 MiB')
        self.app = app
        self.state = state
        self.allow_origins = frozenset(allow_origins)
        self.max_response_bytes = max_response_bytes
        self.required = required

    async def _deny(self, scope, send, detail=_DETAIL):
        body = json.dumps({'detail': detail}, separators=(',', ':')).encode('utf-8')
        headers = [(b'content-type', b'application/json'), (b'content-length', str(len(body)).encode()),
                   (b'retry-after', b'60'), (b'cache-control', b'no-store')]
        origin = next((value for name, value in scope.get('headers', ()) if name.lower() == b'origin'), None)
        if origin is not None and origin.decode('latin-1') in self.allow_origins:
            headers.extend([(b'access-control-allow-origin', origin), (b'vary', b'Origin'),
                            (b'access-control-expose-headers', b'Retry-After')])
        elif origin is not None and '*' in self.allow_origins:
            headers.extend([(b'access-control-allow-origin', b'*'),
                            (b'access-control-expose-headers', b'Retry-After')])
        await send({'type': 'http.response.start', 'status': 503, 'headers': headers})
        await send({'type': 'http.response.body', 'body': b'' if scope.get('method') == 'HEAD' else body,
                    'more_body': False})

    async def __call__(self, scope, receive, send):
        budget = self.state.budget
        if (scope['type'] != 'http'
                or scope.get('path') == '/live' and scope.get('method') in ('GET', 'HEAD')):
            await self.app(scope, receive, send)
            return
        if budget is None:
            if self.required:
                await self._deny(scope, send)
            else:
                await self.app(scope, receive, send)
            return
        try:
            await run_in_threadpool(budget.invocation)
        except _UNAVAILABLE:
            await self._deny(scope, send)
            return

        start = None
        complete = False
        body = bytearray()

        async def capture(message):
            nonlocal start, complete
            if message['type'] == 'http.response.start':
                if start is not None or complete or message.get('trailers'):
                    raise RuntimeError('Invalid or unsupported ASGI response start')
                start = dict(message)
                start['headers'] = list(message.get('headers', ()))
                return
            if message['type'] != 'http.response.body' or start is None or complete:
                raise RuntimeError('Invalid or unsupported ASGI response event')
            chunk = message.get('body', b'')
            if type(chunk) is not bytes:
                raise TypeError('ASGI response body must be bytes')
            if len(body) + len(chunk) > self.max_response_bytes:
                raise _ResponseTooLarge()
            body.extend(chunk)
            complete = not message.get('more_body', False)

        try:
            await self.app(scope, receive, capture)
            if start is None or not complete:
                raise RuntimeError('Application did not complete its ASGI response')
            size = 0 if scope.get('method') == 'HEAD' else len(body)
            await run_in_threadpool(budget.response, size)
        except _ResponseTooLarge:
            await self._deny(scope, send, 'This response exceeds the available size limit. Request a smaller page.')
            return
        except _UNAVAILABLE:
            await self._deny(scope, send)
            return
        await send(start)
        await send({'type': 'http.response.body', 'body': b'' if scope.get('method') == 'HEAD' else bytes(body),
                    'more_body': False})
