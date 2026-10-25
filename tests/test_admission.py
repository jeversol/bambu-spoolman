import asyncio
import unittest
from concurrent.futures import ThreadPoolExecutor

from aiohttp import web

from bambu_spoolman.admission import MutationAdmission


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_fifo_and_cancelled_active_keeps_slot(self):
        gate = MutationAdmission(max_waiting=2)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def operation(value):
            calls.append(value)
            if value == 0:
                started.set()
                await release.wait()

        active = asyncio.create_task(gate.run(lambda: operation(0)))
        await started.wait()
        active.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await active
        queued = [
            asyncio.create_task(gate.run(lambda i=i: operation(i))) for i in (1, 2)
        ]
        await asyncio.sleep(0)
        with self.assertRaises(web.HTTPServiceUnavailable) as error:
            await gate.run(lambda: operation(3))
        self.assertEqual(error.exception.headers["Retry-After"], "1")
        self.assertEqual(calls, [0])
        release.set()
        await asyncio.gather(*queued)
        self.assertEqual(calls, [0, 1, 2])

    async def test_cancelled_queue_never_runs_and_capacity_recovers(self):
        gate = MutationAdmission(max_waiting=1)
        release = asyncio.Event()
        active = asyncio.create_task(gate.run(release.wait))
        await asyncio.sleep(0)
        queued = asyncio.create_task(gate.run(lambda: self.fail("cancelled write ran")))
        await asyncio.sleep(0)
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        replacement = asyncio.create_task(gate.run(release.wait))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(active, replacement)

    async def test_shutdown_rejects_queue_and_new_writes(self):
        gate = MutationAdmission()
        release = asyncio.Event()
        active = asyncio.create_task(gate.run(release.wait))
        await asyncio.sleep(0)
        queued = asyncio.create_task(gate.run(lambda: self.fail("shutdown write ran")))
        await asyncio.sleep(0)
        shutdown = asyncio.create_task(gate.shutdown(None))
        with self.assertRaises(web.HTTPServiceUnavailable):
            await queued
        with self.assertRaises(web.HTTPServiceUnavailable):
            await gate.run(release.wait)
        release.set()
        await asyncio.gather(active, shutdown)

    async def test_waiting_writes_leave_executor_available_for_reads(self):
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=2))
        gate = MutationAdmission()
        import threading

        release = threading.Event()
        started = asyncio.Event()

        async def write():
            started.set()
            await asyncio.to_thread(release.wait, 5)

        active = asyncio.create_task(gate.run(write))
        await started.wait()
        queued = [asyncio.create_task(gate.run(write)) for _ in range(8)]
        try:
            await asyncio.sleep(0)
            self.assertEqual(
                await asyncio.wait_for(asyncio.to_thread(lambda: "read"), 1), "read"
            )
        finally:
            release.set()
            await asyncio.gather(active, *queued)
