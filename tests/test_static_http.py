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
        (self.static / "internal-link.js").symlink_to(
            self.static / "assets/app-hash.js"
        )
        (self.static / "external-directory").symlink_to(root, target_is_directory=True)
        (self.static / ".hidden").mkdir()
        (self.static / ".hidden/asset.js").write_text("secret")
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
            "/internal-link.js",
            "/external-directory/secret.txt",
            "/.hidden/asset.js",
            "/assets/..%2f..%2fsecret.txt",
            "/%2fetc%2fpasswd",
            "/assets/%252e%252e/secret.txt",
            "/%2e%2e/secret.txt",
        ):
            response = await self.client.get(path)
            self.assertEqual(response.status, 404, path)
            self.assertEqual(response.content_type, "application/json")
            self.assertNotIn("secret", await response.text())

    async def test_request_paths_cannot_probe_files_outside_startup_allowlist(self):
        (self.static / "late-file.txt").write_text("not a published asset")
        response = await self.client.get("/late-file.txt")
        self.assertEqual(response.status, 404)
        self.assertNotIn("not a published asset", await response.text())

    async def test_missing_published_asset_does_not_return_html(self):
        (self.static / "assets/app-hash.js").unlink()
        response = await self.client.get("/assets/app-hash.js")
        self.assertEqual(response.status, 404)
        self.assertEqual(response.content_type, "application/json")

    async def test_symlinked_index_is_not_used_for_deep_links(self):
        (self.static / "index.html").unlink()
        (self.static / "index.html").symlink_to(self.static.parent / "secret.txt")
        client = TestClient(TestServer(create_app(Mock(), static_dir=self.static)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        response = await client.get("/ams/1/tray/1")
        self.assertEqual(response.status, 503)
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
