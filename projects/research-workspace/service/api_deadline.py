"""Stop hosted HTTP work when its wall deadline expires, including orphan threads."""
import math
import os
import threading


class APIRequestDeadline:
    def __init__(self, app, *, enabled, seconds=15):
        if type(enabled) is not bool or type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 15:
            raise ValueError('Use an explicit hosted flag and a deadline no greater than 15 seconds')
        self.app, self.enabled, self.seconds = app, enabled, seconds

    async def __call__(self, scope, receive, send):
        if not self.enabled or scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        finished = threading.Event()

        def expire():
            if not finished.wait(self.seconds):
                os._exit(124)

        threading.Thread(target=expire, name='api-request-deadline', daemon=True).start()
        # Cancellation can leave a synchronous worker alive. Only normal return
        # proves that the complete ASGI stack has finished its work.
        await self.app(scope, receive, send)
        finished.set()
