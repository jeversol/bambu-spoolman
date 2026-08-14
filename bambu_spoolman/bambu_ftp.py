import ftplib
import os
import ssl
import tempfile

from loguru import logger

FTP_DOWNLOAD_BLOCK_SIZE = 64 * 1024


class ImplicitFTP_TLS(ftplib.FTP_TLS):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None

    @property
    def sock(self):
        """Return the socket."""
        return self._sock

    @sock.setter
    def sock(self, value):
        """When modifying the socket, ensure that it is ssl wrapped."""
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        """Override the ntransfercmd method"""
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        conn = self.sock.context.wrap_socket(
            conn, server_hostname=self.host, session=self.sock.session
        )
        return conn, size


def retrieve_3mf(filename):
    logger.debug("Retrieving cached 3mf file {}", filename)
    temp_file_name = None
    try:
        with ImplicitFTP_TLS() as ftp:
            ftp.set_pasv(True)
            ftp.connect(os.environ.get("PRINTER_IP"), 990, 5)
            ftp.login("bblp", os.environ.get("PRINTER_ACCESS_CODE"))
            ftp.prot_p()

            # Check if the file exists
            logger.debug("Checking if file {} exists", filename)
            size = ftp.size(filename)
            logger.debug("File {} exists, size: {}", filename, size)

            # Get the file
            logger.debug("Retrieving file {}", filename)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".3mf") as f:
                temp_file_name = f.name
                ftp.retrbinary(
                    f"RETR {filename}",
                    f.write,
                    blocksize=FTP_DOWNLOAD_BLOCK_SIZE,
                )
    except ftplib.all_errors as e:
        logger.error("Failed to retrieve file {}: {}", filename, e)
        if temp_file_name is not None:
            try:
                os.remove(temp_file_name)
            except FileNotFoundError:
                pass
        return None

    logger.debug("File retrieved")
    return temp_file_name
