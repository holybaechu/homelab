from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

import services


class ServiceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_remote_paths_stay_inside_the_selected_space(self):
        for value in ("../secret", "/other-space/file", "notes/../../secret", "notes\\file"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                services.remote_path(value)
        self.assertEqual(services.remote_path("Reports/2026 budget.pdf"), "Reports/2026 budget.pdf")

    def test_sharing_rejects_files_outside_workspace_and_vault(self):
        with tempfile.NamedTemporaryFile() as file, self.assertRaises(ValueError):
            services.local_source(file.name)

    def test_sharing_resolves_symlinks_before_checking_the_root(self):
        with patch.object(Path, "resolve", return_value=Path("/opt/homelab/credentials/ZIPLINE_TOKEN")), \
             patch.object(Path, "is_file", return_value=True), self.assertRaises(ValueError):
            services.local_source("/opt/data/workspace/friendly-name")

    def test_ambiguous_spaces_are_not_selected(self):
        drives = [{"id": "1", "name": "Public"}, {"id": "2", "name": "Public"}]
        with self.assertRaises(ValueError):
            services.select_space(drives, "Public")
        self.assertEqual(services.select_space(drives, "2")["id"], "2")

    def test_cobalt_internal_tunnel_keeps_query_parameters(self):
        self.assertEqual(services.cobalt_url("https://cobalt.hchu.me/api/tunnel?id=a%2Fb&sig=xyz"),
                         "http://cobalt:8080/api/tunnel?id=a%2Fb&sig=xyz")
        with self.assertRaises(ValueError):
            services.cobalt_url("https://username:password@cdn.example/video.mp4")

    async def test_shortening_uses_the_existing_link_domain(self):
        def respond(request):
            self.assertEqual(request.headers["X-Zipline-Domain"], "l.hchu.me")
            self.assertEqual(request.url.path, "/api/user/urls")
            return httpx.Response(200, json={"url": "https://l.hchu.me/test"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            with patch.object(services, "credential", return_value="test-token"):
                result = await services.Services(http).zipline_shorten_url("https://example.com", "test")
        self.assertEqual(result["url"], "https://l.hchu.me/test")

    async def test_failed_download_removes_its_partial_working_copy(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(services, "WORKSPACE", Path(folder)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as http:
                with self.assertRaises(httpx.HTTPStatusError):
                    await services.Services(http).download("https://example.com/missing", "missing.txt")
            self.assertEqual(list(Path(folder).iterdir()), [])

    async def test_upload_cannot_overwrite_an_existing_file(self):
        with tempfile.NamedTemporaryFile() as source:
            source.write(b"test"); source.flush()
            def respond(request):
                if request.method == "MOVE":
                    self.assertEqual(request.headers["Overwrite"], "F")
                    self.assertTrue(request.headers["Destination"].endswith("/existing.txt"))
                    return httpx.Response(412)
                self.assertIn("/.hermes-upload-", request.url.path)
                return httpx.Response(201 if request.method == "PUT" else 204)
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
                api = services.Services(http)
                async def drives():
                    return [{"id": "1", "name": "Personal", "driveType": "personal",
                             "root": {"webDavUrl": services.CLOUD + "/dav/spaces/1"}}]
                with patch.object(api, "drives", drives), patch.object(api, "cloud_auth", return_value=None), \
                     patch.object(services, "local_source", return_value=Path(source.name)):
                    with self.assertRaises(httpx.HTTPStatusError):
                        await api.cloud_upload_file(source.name, "personal", "existing.txt")


if __name__ == "__main__":
    unittest.main()
