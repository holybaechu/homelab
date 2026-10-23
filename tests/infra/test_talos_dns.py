import ipaddress
import json
import os
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]


class TalosDNSControlPath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        artifact = os.environ.get("HOMELAB_TALOS_NETWORK_CONFIG")
        if not artifact:
            raise RuntimeError("Run scripts/validate to test the provider-rendered Talos network configuration")
        cls.network = json.loads(Path(artifact).read_text())
        cls.settings = yaml.safe_load((ROOT / "kubernetes/clusters/homelab/settings.yaml").read_text())["data"]

    def dns_sources(self, protocol):
        rules = [
            document for document in self.network
            if document["kind"] == "NetworkRuleConfig"
            and document["portSelector"]["protocol"] == protocol
            and any(str(port) == "53" for port in document["portSelector"]["ports"])
        ]
        self.assertTrue(rules, f"No explicit {protocol} DNS ingress rule; public port53 must remain restricted")
        return [ipaddress.ip_network(source["subnet"]) for rule in rules for source in rule["ingress"]]

    def test_host_resolver_accepts_cluster_and_private_dns_clients(self):
        clients = {
            "CoreDNS pod": ipaddress.ip_network(self.settings["POD_CIDR"])[2],
            "LAN client": next(ipaddress.ip_network(self.settings["LAN_CIDR"]).hosts()),
            "Headscale client": ipaddress.ip_address("100.64.0.1"),
        }
        for protocol in ("tcp", "udp"):
            sources = self.dns_sources(protocol)
            for name, client in clients.items():
                with self.subTest(protocol=protocol, client=name):
                    self.assertTrue(any(client in source for source in sources), f"{name} cannot reach the Talos host resolver over {protocol}")

    def test_plain_dns_is_not_an_open_public_resolver(self):
        public_client = ipaddress.ip_address("8.8.8.8")
        for protocol in ("tcp", "udp"):
            with self.subTest(protocol=protocol):
                self.assertFalse(any(public_client in source for source in self.dns_sources(protocol)))


if __name__ == "__main__":
    unittest.main()
