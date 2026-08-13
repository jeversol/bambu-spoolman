import ftplib
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from bambu_spoolman.bambu_ftp import retrieve_3mf


class Retrieve3mfTests(unittest.TestCase):
    @patch("bambu_spoolman.bambu_ftp.ImplicitFTP_TLS")
    def test_streams_ftp_download_to_temporary_file(self, ftp_class):
        ftp = MagicMock()
        ftp_class.return_value.__enter__.return_value = ftp
        ftp.size.return_value = 12

        def write_chunks(command, callback, blocksize):
            self.assertEqual(command, "RETR cache/model.3mf")
            self.assertEqual(blocksize, 64 * 1024)
            callback(b"first")
            callback(b"second")

        ftp.retrbinary.side_effect = write_chunks

        model_path = retrieve_3mf("cache/model.3mf")

        self.addCleanup(os.remove, model_path)
        with open(model_path, "rb") as model_file:
            self.assertEqual(model_file.read(), b"firstsecond")

    @patch("bambu_spoolman.bambu_ftp.ImplicitFTP_TLS")
    def test_removes_partial_file_after_ftp_transfer_error(self, ftp_class):
        ftp = MagicMock()
        ftp_class.return_value.__enter__.return_value = ftp
        ftp.size.return_value = 12
        created_files = []
        real_named_temporary_file = tempfile.NamedTemporaryFile

        def track_temporary_file(*args, **kwargs):
            temporary_file = real_named_temporary_file(*args, **kwargs)
            created_files.append(temporary_file.name)
            return temporary_file

        def interrupt_transfer(command, callback, blocksize):
            callback(b"partial")
            raise EOFError("connection interrupted")

        ftp.retrbinary.side_effect = interrupt_transfer

        with patch(
            "bambu_spoolman.bambu_ftp.tempfile.NamedTemporaryFile",
            side_effect=track_temporary_file,
        ):
            model_path = retrieve_3mf("cache/model.3mf")

        self.assertIsNone(model_path)
        self.assertEqual(len(created_files), 1)
        self.assertFalse(os.path.exists(created_files[0]))

    @patch("bambu_spoolman.bambu_ftp.ImplicitFTP_TLS")
    def test_returns_none_when_ftp_connection_fails(self, ftp_class):
        ftp = MagicMock()
        ftp_class.return_value.__enter__.return_value = ftp
        ftp.connect.side_effect = OSError("connection refused")

        self.assertIsNone(retrieve_3mf("cache/model.3mf"))

    @patch("bambu_spoolman.bambu_ftp.ImplicitFTP_TLS")
    def test_returns_none_when_remote_file_does_not_exist(self, ftp_class):
        ftp = MagicMock()
        ftp_class.return_value.__enter__.return_value = ftp
        ftp.size.side_effect = ftplib.error_perm("550 file unavailable")

        self.assertIsNone(retrieve_3mf("cache/missing.3mf"))
        ftp.retrbinary.assert_not_called()


if __name__ == "__main__":
    unittest.main()
