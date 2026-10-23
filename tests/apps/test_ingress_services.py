import json
import os
from pathlib import Path
import unittest
from urllib.parse import urlsplit


class IngressServices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        artifact = os.environ.get("HOMELAB_RENDERED")
        if not artifact:
            raise RuntimeError("Run scripts/validate to test authored and Helm-rendered services")
        cls.resources = json.loads(Path(artifact).read_text())
        cls.services = {
            (item["metadata"].get("namespace", "default"), item["metadata"]["name"]): item
            for item in cls.resources if item["kind"] == "Service"
        }

    def assert_service_port(self, namespace, name, port):
        key = (namespace, name)
        self.assertIn(key, self.services, f"Kubernetes Service {namespace}/{name} is missing from the rendered configuration")
        ports = self.services[key]["spec"].get("ports", [])
        exposed = [value for item in ports for value in (item["port"], item.get("name")) if value is not None]
        self.assertIn(port, exposed, f"Service {namespace}/{name} exposes {exposed}, not {port}; targetPort is not a Service port")

    def test_ingress_routes_use_exposed_kubernetes_service_ports(self):
        checked = 0
        for item in self.resources:
            if item["kind"] != "IngressRoute":
                continue
            metadata = item["metadata"]
            namespace = metadata.get("namespace", "default")
            for route in item["spec"]["routes"]:
                for service in route.get("services", []):
                    if service.get("kind", "Service") != "Service":
                        continue
                    with self.subTest(route=f"{namespace}/{metadata['name']}", match=route["match"], service=service["name"]):
                        self.assert_service_port(service.get("namespace", namespace), service["name"], service.get("port"))
                    checked += 1
        self.assertGreater(checked, 0)

    def test_forward_auth_uses_an_exposed_kubernetes_service_port(self):
        checked = 0
        for item in self.resources:
            if item["kind"] != "Middleware" or "forwardAuth" not in item["spec"]:
                continue
            address = urlsplit(item["spec"]["forwardAuth"]["address"])
            host = (address.hostname or "").split(".")
            if len(host) < 3 or host[2] != "svc":
                continue
            port = address.port or {"http": 80, "https": 443}[address.scheme]
            with self.subTest(middleware=item["metadata"]["name"]):
                self.assert_service_port(host[1], host[0], port)
            checked += 1
        self.assertGreater(checked, 0)

    def test_private_ingress_preserves_vpn_sources_without_exposing_management_ports(self):
        deployment = next(item for item in self.resources if item["kind"] == "Deployment" and item["metadata"]["name"] == "traefik")
        pod = deployment["spec"]["template"]["spec"]
        self.assertTrue(pod.get("hostNetwork"), "Host-port DNAT and gateway masquerading hide VPN clients behind the CNI address")
        arguments = pod["containers"][0]["args"]
        for name, address in {"web": ":80/tcp", "websecure": ":443/tcp", "traefik": "127.0.0.1:8080/tcp", "metrics": "127.0.0.1:9100/tcp"}.items():
            self.assertIn(f"--entryPoints.{name}.address={address}", arguments)
        self.assertFalse(any("forwardedHeaders.insecure" in argument for argument in arguments))
        middleware = next(item for item in self.resources if item["kind"] == "Middleware" and item["metadata"]["name"] == "private-only")
        self.assertEqual(set(middleware["spec"]["ipAllowList"]["sourceRange"]), {"192.168.0.0/24", "100.64.0.0/10", "fd7a:115c:a1e0::/48"})


if __name__ == "__main__":
    unittest.main()
