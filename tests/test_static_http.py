import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from aiohttp.test_utils import TestClient, TestServer

from bambu_spoolman.http_api import create_app


class StaticHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.static = root / "static"
        self.static.mkdir()
        (self.static / "assets").mkdir()
        (self.static / "index.html").write_text('<div id="root"></div>')
        (self.static / "assets/app-hash.js").write_text("console.log('ready')")
        (self.static / "assets/app-hash.css").write_text("body{color:red}")
        (self.static / ".private").write_text("secret")
        (root / "secret.txt").write_text("secret")
        (self.static / "escape.txt").symlink_to(root / "secret.txt")
        self.client = TestClient(TestServer(create_app(Mock(), static_dir=self.static)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_deep_links_serve_revalidating_html(self):
        for path in ("/", "/external-spool", "/ams/1/tray/1", "/ams/12/tray/4"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 200, path)
            self.assertIn('id="root"', await response.text())
            self.assertEqual(response.headers["Cache-Control"], "no-cache")
            self.assertEqual(response.content_type, "text/html")

    async def test_assets_have_correct_mime_and_immutable_cache(self):
        for extension, mime in (("js", "text/javascript"), ("css", "text/css")):
            response = await self.client.get(f"/assets/app-hash.{extension}")
            self.assertEqual(response.status, 200)
            self.assertEqual(response.content_type, mime)
            self.assertIn("immutable", response.headers["Cache-Control"])

    async def test_missing_assets_api_and_unsafe_paths_do_not_fall_back_to_html(self):
        for path in (
            "/assets/missing.js",
            "/api/v1/missing",
            "/api",
            "/missing",
            "/ams/0/tray/1",
            "/ams/1/tray/5",
            "/.private",
            "/escape.txt",
            "/%2e%2e/secret.txt",
        ):
            response = await self.client.get(path)
            self.assertEqual(response.status, 404, path)
            self.assertEqual(response.content_type, "application/json")
            self.assertNotIn("secret", await response.text())

    async def test_api_writes_reject_foreign_null_and_malformed_origins(self):
        for origin in (
            "https://other.invalid",
            "http://[malformed",
            "null",
            "file://local",
            str(self.client.make_url("/evil")),
        ):
            response = await self.client.put(
                "/api/v1/trays/0", json={"spoolId": -1}, headers={"Origin": origin}
            )
            self.assertEqual(response.status, 403, origin)
            self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_missing_deployment_assets_fail_explicitly(self):
        (self.static / "index.html").unlink()
        response = await self.client.get("/")
        self.assertEqual(response.status, 503)
