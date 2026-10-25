"""Bound API mutations without occupying workers while waiting for admission."""

import asyncio
from collections import deque

from aiohttp import web
from loguru import logger


class MutationAdmission:
    def __init__(self, max_waiting=8):
        self.max_waiting = max_waiting
        self.waiting = deque()
        self.active = None
        self.reserved = False
        self.closed = False

    @staticmethod
    def busy():
        return web.HTTPServiceUnavailable(
            reason="Mapping writes are busy; refresh before trying again",
            headers={"Retry-After": "1"},
        )

    async def run(self, operation):
        if self.closed:
            raise self.busy()
        if self.reserved:
            if len(self.waiting) >= self.max_waiting:
                raise self.busy()
            ready = asyncio.get_running_loop().create_future()
            self.waiting.append(ready)
            try:
                await ready
            except BaseException:
                if ready in self.waiting:
                    self.waiting.remove(ready)
                elif not ready.cancelled() and ready.exception() is None:
                    self._advance()
                raise
        else:
            self.reserved = True
        if self.closed:
            self._advance()
            raise self.busy()

        async def execute():
            return await operation()

        task = asyncio.create_task(execute())
        self.active = task
        task.add_done_callback(self._finished)
        # Cancellation of an HTTP handler cannot cancel an already-started
        # mutation: to_thread cancellation does not stop its synchronous worker.
        return await asyncio.shield(task)

    def _finished(self, task):
        # Observe exceptions even when the requesting connection has gone away.
        if not task.cancelled():
            task.exception()
        self.active = None
        self._advance()

    def _advance(self):
        while self.waiting:
            ready = self.waiting.popleft()
            if not ready.done():
                ready.set_result(None)
                return
        self.reserved = False

    async def shutdown(self, app):
        self.closed = True
        while self.waiting:
            ready = self.waiting.popleft()
            if not ready.done():
                ready.set_exception(self.busy())
        if self.active is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self.active), timeout=5)
            except TimeoutError:
                logger.warning("event=http_mutation_drain_pending")
            except Exception:
                # Handler middleware reports operation failures to live callers.
                pass
