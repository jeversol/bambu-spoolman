import asyncio
import signal
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from bambu_spoolman.daemon import async_main


class DaemonShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def run_service(self, fail=False):
        handlers = {}
        order = []
        mqtt = Mock()
        mqtt.is_alive.return_value = False
        mqtt.message_dispatcher.is_alive.return_value = False
        mqtt.stop.side_effect = lambda: order.append("mqtt_stop")
        tracker = Mock()
        tracker.stop.side_effect = lambda: order.append("tracker_stop")
        tracker.close.side_effect = lambda **kwargs: (
            order.append("tracker_close") or True
        )

        async def server():
            if fail:
                raise OSError("listener failed")
            asyncio.get_running_loop().call_soon(handlers[signal.SIGTERM])
            await asyncio.Event().wait()

        loop = asyncio.get_running_loop()
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    loop,
                    "add_signal_handler",
                    side_effect=lambda sig, fn: handlers.__setitem__(sig, fn),
                )
            )
            remove = stack.enter_context(patch.object(loop, "remove_signal_handler"))
            stack.enter_context(
                patch("bambu_spoolman.daemon.MqttHandler", return_value=mqtt)
            )
            stack.enter_context(
                patch(
                    "bambu_spoolman.daemon.FilamentUsageTracker", return_value=tracker
                )
            )
            stack.enter_context(patch("bambu_spoolman.daemon.stateful_printer_info"))
            stack.enter_context(
                patch("bambu_spoolman.daemon.AutomaticSpoolSwitch.get_instance")
            )
            stack.enter_context(
                patch("bambu_spoolman.daemon.run_http_server", side_effect=server)
            )
            if fail:
                with self.assertRaisesRegex(OSError, "listener failed"):
                    await asyncio.wait_for(async_main(), 5)
            else:
                await asyncio.wait_for(async_main(), 5)
        self.assertEqual(order, ["mqtt_stop", "tracker_stop", "tracker_close"])
        tracker.close.assert_called_once_with(timeout=30)
        self.assertEqual(remove.call_count, 2)
        mqtt.join.assert_called_once_with(6)
        mqtt.message_dispatcher.join.assert_called_once_with(1)

    async def test_signal_shutdown_closes_tracker_before_joining_mqtt(self):
        await self.run_service()

    async def test_listener_failure_also_closes_tracker(self):
        await self.run_service(fail=True)
