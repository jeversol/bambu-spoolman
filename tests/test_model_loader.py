import functools
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from bambu_spoolman.broker.model_loader import download_model, retrieve_model
from bambu_spoolman.model_limits import ModelLimits, get_model_limits


class ModelLoaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.temporary_file = functools.partial(
            tempfile.NamedTemporaryFile, dir=self.directory.name
        )
        self.response = Mock()
        self.response.headers = {}

    def download(self, chunks, **limits):
        self.response.iter_content.return_value = chunks
        with (
            patch(
                "bambu_spoolman.broker.model_loader.requests.get",
                return_value=self.response,
            ),
            patch(
                "bambu_spoolman.broker.model_loader.tempfile.NamedTemporaryFile",
                self.temporary_file,
            ),
        ):
            return download_model(
                "https://printer/model.3mf", limits=ModelLimits(**limits)
            )

    def test_http_stream_at_exact_limit_is_preserved(self):
        path = self.download([b"abc", b"def"], download_bytes=6)
        with open(path, "rb") as model:
            self.assertEqual(model.read(), b"abcdef")
        self.response.close.assert_called_once_with()

    def test_http_over_budget_stream_is_closed_and_partial_file_deleted(self):
        self.assertIsNone(self.download([b"abc", b"def"], download_bytes=5))
        self.assertEqual(os.listdir(self.directory.name), [])
        self.response.close.assert_called_once_with()

    def test_http_oversized_header_is_rejected_without_reading_body(self):
        self.response.headers = {"Content-Length": "9"}
        self.assertIsNone(self.download([b"abc"], download_bytes=5))
        self.response.iter_content.assert_not_called()
        self.assertEqual(os.listdir(self.directory.name), [])

    def test_http_false_size_header_cannot_bypass_stream_limit(self):
        self.response.headers = {"Content-Length": "1"}
        self.assertIsNone(self.download([b"abcdef"], download_bytes=5))
        self.assertEqual(os.listdir(self.directory.name), [])

    def test_http_cooperative_deadline_removes_partial_file(self):
        clock = Mock(return_value=0)

        def chunks():
            yield b"a"
            clock.return_value = 2
            yield b"b"

        with patch("bambu_spoolman.model_limits.time.monotonic", clock):
            self.assertIsNone(self.download(chunks(), seconds=1))
        self.assertEqual(os.listdir(self.directory.name), [])
        self.response.close.assert_called_once_with()

    @patch("bambu_spoolman.broker.model_loader.retrieve_3mf")
    def test_printer_url_schemes_keep_mount_prefix_normalization(self, retrieve):
        retrieve.return_value = "downloaded.3mf"
        for url in (
            "file:///sdcard/cache/model.3mf",
            "ftp:///media/usb0/cache/model.3mf",
            "brtc://cache/model.3mf",
        ):
            self.assertEqual(retrieve_model(url), "downloaded.3mf")
            retrieve.assert_called_with("cache/model.3mf")
        self.assertIsNone(retrieve_model("unsupported://model"))
        self.assertIsNone(retrieve_model(None))

    def test_model_limits_are_positive_finite_and_operator_configurable(self):
        with patch.dict(
            os.environ,
            {
                "BAMBU_SPOOLMAN_MODEL_DOWNLOAD_BYTES": "123",
                "BAMBU_SPOOLMAN_MODEL_SECONDS": "1.5",
            },
        ):
            limits = get_model_limits()
        self.assertEqual(limits.download_bytes, 123)
        self.assertEqual(limits.seconds, 1.5)
        for value in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                ModelLimits(seconds=value)

    @patch("bambu_spoolman.broker.model_loader.requests.get")
    def test_model_download_uses_configured_timeout(self, get):
        get.return_value.iter_content.return_value = [b"model"]

        with patch.dict(os.environ, {"BAMBU_SPOOLMAN_HTTP_TIMEOUT": "12.5"}):
            model_path = download_model("http://printer/model.3mf")

        self.addCleanup(os.remove, model_path)
        get.assert_called_once_with(
            "http://printer/model.3mf", timeout=12.5, stream=True
        )

    @patch("bambu_spoolman.broker.model_loader.requests.get")
    def test_model_download_writes_streamed_chunks(self, get):
        get.return_value.iter_content.return_value = [b"first", b"", b"second"]

        model_path = download_model("http://printer/model.3mf")

        self.addCleanup(os.remove, model_path)
        with open(model_path, "rb") as model_file:
            self.assertEqual(model_file.read(), b"firstsecond")
        get.return_value.raise_for_status.assert_called_once_with()
        get.return_value.iter_content.assert_called_once_with(chunk_size=64 * 1024)
        get.return_value.close.assert_called_once_with()

    @patch("bambu_spoolman.broker.model_loader.requests.get")
    def test_model_download_removes_partial_file_after_stream_error(self, get):
        created_files = []
        real_named_temporary_file = tempfile.NamedTemporaryFile

        def track_temporary_file(*args, **kwargs):
            temporary_file = real_named_temporary_file(*args, **kwargs)
            created_files.append(temporary_file.name)
            return temporary_file

        get.return_value.iter_content.side_effect = (
            requests.exceptions.ChunkedEncodingError("connection interrupted")
        )

        with patch(
            "bambu_spoolman.broker.model_loader.tempfile.NamedTemporaryFile",
            side_effect=track_temporary_file,
        ):
            model_path = download_model("http://printer/model.3mf")

        self.assertIsNone(model_path)
        self.assertEqual(len(created_files), 1)
        self.assertFalse(os.path.exists(created_files[0]))
        get.return_value.close.assert_called_once_with()
