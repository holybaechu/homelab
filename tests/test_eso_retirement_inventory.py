import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from typing import Any


spec = importlib.util.spec_from_file_location(
    "inventory", Path(__file__).resolve().parents[1] / "scripts/inventory-eso-retirement.py")
assert spec is not None and spec.loader is not None
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


class RetirementInventoryTests(unittest.TestCase):
    def test_nested_reference_interfaces_not_arbitrary_names(self):
        value = {"template": {"spec": {
            "volumes": [{"secret": {"secretName": "hermes-services"}}],
            "containers": [{"envFrom": [{"secretRef": {"name": "mysql-env"}}],
                            "env": [{"valueFrom": {"secretKeyRef": {"name": "n8n-env"}}}]}],
            "imagePullSecrets": [{"name": "hermes-web"}],
        }}, "name": "immich", "claimName": "immich",
            "existingSecret": "langfuse-env"}
        self.assertEqual(inventory.old_references(value, "homelab"), {
            ("homelab", name) for name in
            ("hermes-services", "mysql-env", "n8n-env", "hermes-web", "langfuse-env")})
        self.assertEqual(inventory.old_references(value, "unrelated"), set())

    def test_clusterissuer_and_flux_substitution_references(self):
        self.assertEqual(inventory.old_references({"solvers": [{"dns01": {
            "cloudflare": {"apiTokenSecretRef": {"name": "cloudflare-dns"}}}}]}, "cert-manager"),
                         {("cert-manager", "cloudflare-dns")})
        self.assertEqual(inventory.old_references({"postBuild": {"substituteFrom": [
            {"kind": "Secret", "name": "mysql-env"}]}}, "homelab"),
                         {("homelab", "mysql-env")})

    def test_owner_identity_and_deletion_are_required(self):
        # Operator 1.12.1 does not set the owner reference's controller bit.
        metadata: dict[str, Any] = {"ownerReferences": [{"kind": "OnePasswordItem", "uid": "new",
                                         "name": "replacement", "apiVersion": "onepassword.com/v1"}]}
        self.assertTrue(inventory.owner_matches(metadata, "OnePasswordItem", "new", "replacement"))
        self.assertFalse(inventory.owner_matches(metadata, "OnePasswordItem", "old", "replacement"))
        self.assertFalse(inventory.owner_matches(metadata, "OnePasswordItem", "new", "different"))
        self.assertFalse(inventory.owner_matches({**metadata, "deletionTimestamp": "now"},
                                                 "OnePasswordItem", "new", "replacement"))
        metadata["ownerReferences"][0].update(kind="ExternalSecret", apiVersion="external-secrets.io/v1")
        self.assertFalse(inventory.owner_matches(metadata, "ExternalSecret", "new", "replacement"))
        metadata["ownerReferences"][0]["controller"] = True
        self.assertTrue(inventory.owner_matches(metadata, "ExternalSecret", "new", "replacement"))

    def test_cli_failure_does_not_echo_sensitive_stdout_or_stderr(self):
        with patch.object(inventory.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, b"sensitive stdout", b"sensitive stderr")), self.assertRaises(inventory.ReadFailure):
            inventory.kubectl("get", "pods")

    def test_discovered_unknown_kind_blocks_inventory(self):
        def fixture(*args):
            if args == ("get", "--raw", "/apis"):
                return {"groups": [{"name": group,
                                    "preferredVersion": {"groupVersion": group + "/v1"},
                                    "versions": [{"groupVersion": group + "/v1"}]}
                                   for group in inventory.GROUPS]}
            if "--raw" in args:
                name = "externalsecrets" if args[-1].startswith("/apis/external-secrets.io/") else "futuregenerators"
                return {"resources": [{"name": name, "namespaced": True, "verbs": ["list"]}]}
            if args[1] == "externalsecrets.external-secrets.io":
                return {"items": [{"metadata": {"namespace": ns, "name": name, "uid": name},
                                   "spec": {"target": {"name": name}}}
                                  for ns, names in inventory.TARGETS.items() for name in names]}
            if args[1] == "futuregenerators.generators.external-secrets.io":
                return {"items": [{"metadata": {"namespace": "other", "name": "unreviewed"}}]}
            if args[1] == "onepassworditem":
                ns, name = args[4], args[2]
                return {"metadata": {"uid": name},
                        "spec": {"itemPath": f"vaults/{inventory.VAULT}/items/{inventory.ITEMS[ns][name]}"},
                        "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
            if args[1] == "secret":
                ns, name = args[4], args[2]
                operator = name in inventory.ITEMS[ns]
                return {"ownerReferences": [{"uid": name, "name": name, "controller": not operator,
                                             "kind": "OnePasswordItem" if operator else "ExternalSecret",
                                             "apiVersion": "onepassword.com/v1" if operator else "external-secrets.io/v1"}]}
            if args[1] == "clusterissuers.cert-manager.io":
                return {"items": [{"kind": "ClusterIssuer", "metadata": {"name": "letsencrypt"},
                                   "spec": {"acme": {"solvers": [{"dns01": {"cloudflare": {
                                       "apiTokenSecretRef": {"name": "cloudflare-connect", "key": "credential"}}}}]}}}]}
            return {"items": []}

        output = io.StringIO()
        with patch.object(inventory, "kubectl", side_effect=fixture), contextlib.redirect_stdout(output):
            self.assertTrue(inventory.main())
        self.assertIn("futuregenerators count=1", output.getvalue())
        self.assertIn("FAIL reviewed-store-or-empty-kind", output.getvalue())
        self.assertEqual(output.getvalue().count("FAIL "), 1)

    def test_failed_reads_fail_closed_and_main_only_reads(self):
        output = io.StringIO()
        calls = []

        def denied(*args):
            calls.append(args)
            raise inventory.ReadFailure("sensitive exception")

        with patch.object(inventory, "kubectl", side_effect=denied), contextlib.redirect_stdout(output):
            self.assertTrue(inventory.main())
        self.assertNotIn("sensitive", output.getvalue())
        self.assertTrue(calls)
        self.assertTrue(all(args[0] == "get" for args in calls))


class PostPruneTests(unittest.TestCase):
    def run_phase(self, phase, leftover=None, installed=True, stale=False):
        def fixture(*args):
            if args == ("get", "--raw", "/apis"):
                return {"groups": [{"name": group,
                                    "preferredVersion": {"groupVersion": group + "/v1"},
                                    "versions": [{"groupVersion": group + "/v1"}]}
                                   for group in inventory.GROUPS] if installed else []}
            if "--raw" in args:
                return {"resources": [{"name": "clustersecretstores", "namespaced": False,
                                       "verbs": ["list"]}]}
            if args[1].endswith(".external-secrets.io"):
                entries = []
                if phase == "targets-removed" and args[1] == "clustersecretstores.external-secrets.io":
                    entries.append({"metadata": {"name": "onepassword"}})
                if leftover == "instance":
                    entries.append({"metadata": {"name": "unexpected"}})
                return {"items": entries}
            if args[1] == "onepassworditem":
                ns, name = args[4], args[2]
                return {"metadata": {"uid": name},
                        "spec": {"itemPath": f"vaults/{inventory.VAULT}/items/{inventory.ITEMS[ns][name]}"},
                        "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
            if args[1] == "secret":
                return {"ownerReferences": [{"uid": args[2], "name": args[2],
                                             "kind": "OnePasswordItem", "apiVersion": "onepassword.com/v1"}]}
            if args[1] == "clusterissuers.cert-manager.io":
                return {"items": [{"kind": "ClusterIssuer", "metadata": {"name": "letsencrypt"},
                                   "spec": {"acme": {"solvers": [{"dns01": {"cloudflare": {
                                       "apiTokenSecretRef": {"name": "cloudflare-connect", "key": "credential"}}}}]}}}]}
            if args[1] in {"helmrelease", "deployment"}:
                return {"metadata": {"name": args[2], "generation": 2}, "spec": {"replicas": 1},
                        "status": {"observedGeneration": 1 if stale else 2, "readyReplicas": 1,
                                   "conditions": [{"type": "Ready", "status": "True"}]}}
            if args[1] == "kustomizations.kustomize.toolkit.fluxcd.io" and "-n" in args:
                return {"items": [{"metadata": {"generation": 2}, "spec": {},
                                   "status": {"conditions": [{"type": "Ready", "status": "True",
                                                               "observedGeneration": 2}]}}]}
            if args[1] == "helmreleases.helm.toolkit.fluxcd.io" and leftover == "release":
                return {"items": [{"kind": "HelmRelease", "metadata": {
                    "name": "external-secrets", "namespace": "external-secrets"}, "spec": {}}]}
            if args[1] == "validatingwebhookconfigurations" and leftover == "webhook":
                return {"items": [{"kind": "ValidatingWebhookConfiguration", "metadata": {
                    "name": "secretstore-validate", "annotations": {
                        "meta.helm.sh/release-name": "external-secrets",
                        "meta.helm.sh/release-namespace": "external-secrets"}}}]}
            return {"items": []}

        output = io.StringIO()
        with patch.object(inventory, "kubectl", side_effect=fixture), contextlib.redirect_stdout(output):
            failed = inventory.main(phase)
        return failed, output.getvalue()

    def test_post_prune_phases_pass_with_only_expected_remaining_resources(self):
        for phase, installed in (("targets-removed", True), ("uninstalled", True),
                                 ("uninstalled", False), ("complete", False)):
            with self.subTest(phase=phase, installed=installed):
                failed, output = self.run_phase(phase, installed=installed)
                self.assertFalse(failed, output)
                self.assertIn("PASS preserved-reloader deployment", output)
                self.assertIn("PASS Flux-kustomizations-ready", output)

    def test_remaining_instances_or_chart_resources_and_stale_reloader_block(self):
        for phase, leftover in (("targets-removed", "instance"), ("uninstalled", "instance"),
                                ("uninstalled", "release"), ("uninstalled", "webhook")):
            with self.subTest(phase=phase, leftover=leftover):
                self.assertTrue(self.run_phase(phase, leftover=leftover)[0])
        self.assertTrue(self.run_phase("complete", installed=True)[0])
        self.assertTrue(self.run_phase("complete", installed=False, stale=True)[0])


if __name__ == "__main__":
    unittest.main()
