import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


loader = importlib.machinery.SourceFileLoader("cluster", str(Path(__file__).resolve().parents[1] / "scripts/cluster"))
spec = importlib.util.spec_from_loader(loader.name, loader)
cluster = importlib.util.module_from_spec(spec)
loader.exec_module(cluster)


class BootstrapSecrets(unittest.TestCase):
    def test_existing_vault_item_is_never_overwritten_by_initial_setup(self):
        with patch.object(cluster, "run", return_value='[{"title":"homelab"}]') as run, patch("getpass.getpass") as prompt:
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                cluster.prepare_secrets(SimpleNamespace(email="owner@example.test"), {"ONEPASSWORD_VAULT": "Homelab"})
            self.assertEqual(run.call_count, 1)
            prompt.assert_not_called()

    def test_secrets_go_to_onepassword_over_stdin_and_are_not_printed(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return "[]" if command[1:3] == ["item", "list"] else "{}"

        output = io.StringIO()
        with patch.object(cluster, "run", side_effect=fake_run), patch("getpass.getpass", side_effect=["fixture-dns-token", "fixture-ddns-token"]), contextlib.redirect_stdout(output):
            cluster.prepare_secrets(SimpleNamespace(email="owner@example.test"), {"ONEPASSWORD_VAULT": "Homelab"})
        command, kwargs = calls[-1]
        item = json.loads(kwargs["payload"])
        fields = {field["label"]: field["value"] for field in item["fields"]}
        self.assertEqual(fields["cloudflare_dns_token"], "fixture-dns-token")
        self.assertEqual(fields["cloudflare_ddns_token"], "fixture-ddns-token")
        self.assertEqual(len(item["fields"]), len(fields))
        for secret in fields.values():
            if secret and secret != "owner@example.test" and len(secret) > 16:
                self.assertNotIn(secret, " ".join(command))
                self.assertNotIn(secret, output.getvalue())

    def test_missing_service_account_blocks_bootstrap_before_any_cluster_write(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(cluster, "apply") as apply:
            with self.assertRaisesRegex(RuntimeError, "OP_SERVICE_ACCOUNT_TOKEN"):
                cluster.bootstrap(SimpleNamespace(git_ref="main"), {})
            apply.assert_not_called()

    def test_wrong_cluster_context_blocks_gateway_registration(self):
        with patch.object(cluster, "check_context", side_effect=RuntimeError("wrong cluster")), patch.object(cluster, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "wrong cluster"):
                cluster.register_gateway({})
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
