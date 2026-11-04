"""Exercise the shared scanner installer without network access."""

import hashlib
import io
import os
import re
import subprocess
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TrivyInstallTests(unittest.TestCase):
    def install(self, *, corrupt=False, download_failure=False):
        action = (ROOT / ".github/actions/scan-image/action.yaml").read_text()
        step = action.split("    - name: Install Trivy from GitHub Releases\n", 1)[1]
        command = re.search(r"      run: \|\n((?:        .*\n|\n)+)", step)[1]
        version = re.search(r"TRIVY_VERSION: v(\S+)", step)[1]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            binary = b'#!/bin/sh\necho "Trivy fixture"\n'
            archive = folder / f"trivy_{version}_Linux-64bit.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                entry = tarfile.TarInfo("trivy")
                entry.size = len(binary)
                entry.mode = 0o755
                tar.addfile(entry, io.BytesIO(binary))
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            if corrupt:
                checksum = "0" * 64
            (folder / f"trivy_{version}_checksums.txt").write_text(
                f"{checksum}  {archive.name}\n"
            )
            for name, body in {
                "uname": "#!/bin/sh\necho x86_64\n",
                "curl": (
                    "#!/bin/sh\nexit 6\n"
                    if download_failure
                    else '#!/bin/sh\nwhile [ "$1" != --output ]; do shift; done\n'
                    'cp "$FIXTURES/${3##*/}" "$2"\n'
                ),
            }.items():
                script = folder / name
                script.write_text(body)
                script.chmod(0o755)
            github_path = folder / "github-path"
            result = subprocess.run(
                ["/bin/sh", "-e", "-c", textwrap.dedent(command)],
                env={
                    **os.environ,
                    "PATH": f"{folder}:{os.environ['PATH']}",
                    "FIXTURES": directory,
                    "RUNNER_TEMP": directory,
                    "TRIVY_VERSION": f"v{version}",
                    "GITHUB_PATH": str(github_path),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            return result, github_path.exists()

    def test_verified_binary_is_executable_and_added_to_path(self):
        result, published = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Trivy fixture", result.stdout)
        self.assertTrue(published)

    def test_checksum_mismatch_prevents_installation(self):
        result, published = self.install(corrupt=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Trivy fixture", result.stdout)
        self.assertFalse(published)

    def test_download_failure_prevents_installation(self):
        result, published = self.install(download_failure=True)
        self.assertEqual(result.returncode, 6)
        self.assertFalse(published)
