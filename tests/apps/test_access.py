import json
import os
from pathlib import Path
import unittest


class ApplicationAccess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        artifact = os.environ.get("HOMELAB_RENDERED")
        if not artifact:
            raise RuntimeError("Run scripts/validate to test the actual rendered resources")
        cls.resources = json.loads(Path(artifact).read_text())

    def resource(self, kind, name, namespace):
        matches = [item for item in self.resources if item["kind"] == kind and item["metadata"]["name"] == name and item["metadata"].get("namespace") == namespace]
        self.assertEqual(len(matches), 1, (kind, name, namespace))
        return matches[0]

    def test_public_dns_exposes_only_queries_and_console_requires_private_access(self):
        routes = self.resource("IngressRoute", "adguard", "networking")["spec"]["routes"]
        public = [route for route in routes if "dns.hchu.me" in route["match"]]
        self.assertEqual(len(public), 1)
        self.assertIn("Path(`/dns-query`)", public[0]["match"])
        self.assertIn("Method(`GET`)", public[0]["match"])
        self.assertIn("Method(`POST`)", public[0]["match"])
        self.assertNotIn("PathPrefix", public[0]["match"])
        console = [route for route in routes if "adguard.hchu.me" in route["match"]]
        self.assertEqual(len(console), 1)
        self.assertIn({"name": "private-only", "namespace": "traefik"}, console[0]["middlewares"])

    def test_copyparty_browser_identity_is_sanitized_before_authentication(self):
        routes = self.resource("IngressRoute", "copyparty", "homelab")["spec"]["routes"]
        protected = next(route for route in routes if route["match"] == "Host(`copyparty.hchu.me`)")
        names = [middleware["name"] for middleware in protected["middlewares"]]
        self.assertLess(names.index("clear-copyparty-identity"), names.index("authentik"))
        anonymous = next(route for route in routes if "Path(`/public`)" in route["match"])
        self.assertIn("Method(`GET`)", anonymous["match"])
        self.assertIn("Method(`HEAD`)", anonymous["match"])
        self.assertNotIn("authentik", [middleware["name"] for middleware in anonymous["middlewares"]])
        self.assertIn("clear-copyparty-identity", [middleware["name"] for middleware in anonymous["middlewares"]])
        dav = next(route for route in routes if "copyparty-dav.hchu.me" in route["match"])
        self.assertEqual([middleware["name"] for middleware in dav["middlewares"]], ["private-only", "clear-copyparty-identity", "secure-headers"])

    def test_shared_files_are_read_only_in_copyparty_and_state_is_separate(self):
        pod = self.resource("Deployment", "copyparty", "homelab")["spec"]["template"]["spec"]
        mounts = {mount["mountPath"]: mount for mount in pod["containers"][0]["volumeMounts"]}
        for path in ("/srv/shared-readonly", "/srv/downloads"):
            self.assertTrue(mounts[path]["readOnly"])
        volumes = {volume["name"]: volume for volume in pod["volumes"]}
        claims = [volumes[mounts[path]["name"]]["persistentVolumeClaim"]["claimName"] for path in ("/srv/public", "/srv/shared-readonly", "/srv/downloads", "/config/state")]
        self.assertEqual(len(set(claims)), 4)

    def test_identity_and_headscale_control_are_reachable_without_proxy_login(self):
        for item in self.resources:
            if item["kind"] != "IngressRoute":
                continue
            for route in item["spec"]["routes"]:
                if any(f"Host(`{host}.hchu.me`)" in route["match"] for host in ("auth", "headscale")):
                    self.assertNotIn("authentik", [middleware["name"] for middleware in route.get("middlewares", [])])

    def test_durable_storage_does_not_reclaim_data_with_a_claim(self):
        classes = [item for item in self.resources if item["kind"] == "StorageClass" and item["metadata"]["name"] == "local-path"]
        self.assertEqual(len(classes), 1)
        self.assertEqual(classes[0]["reclaimPolicy"], "Retain")
        for item in self.resources:
            if item["kind"] == "PersistentVolumeClaim":
                self.assertEqual(item["spec"]["storageClassName"], "local-path")


if __name__ == "__main__":
    unittest.main()
