"""Guard the storage overlay when the upstream release ref changes."""

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LocalPathOverlayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rendered = subprocess.check_output(
            ["kustomize", "build", "kubernetes/platform/controllers/local-path"], cwd=ROOT,
        )
        documents = subprocess.check_output(
            ["yq", "-N", "-o=json", "-I=0", "."], input=rendered,
        )
        cls.resources = {
            (doc["kind"], doc["metadata"]["name"]): doc
            for doc in map(json.loads, documents.splitlines())
        }

    def test_resource_identity_and_namespace_ownership(self):
        self.assertEqual(set(self.resources), {
            ("ServiceAccount", "local-path-provisioner-service-account"),
            ("Role", "local-path-provisioner-role"),
            ("ClusterRole", "local-path-provisioner-role"),
            ("RoleBinding", "local-path-provisioner-bind"),
            ("ClusterRoleBinding", "local-path-provisioner-bind"),
            ("Deployment", "local-path-provisioner"),
            ("StorageClass", "local-path"),
            ("ConfigMap", "local-path-config"),
        })

    def test_retention_and_node_path(self):
        storage = self.resources["StorageClass", "local-path"]
        self.assertEqual(storage["reclaimPolicy"], "Retain")
        self.assertEqual(storage["volumeBindingMode"], "WaitForFirstConsumer")
        self.assertEqual(storage["provisioner"], "rancher.io/local-path")
        self.assertEqual(storage["metadata"]["annotations"][
            "storageclass.kubernetes.io/is-default-class"], "true")
        config = self.resources["ConfigMap", "local-path-config"]["data"]
        self.assertEqual(json.loads(config["config.json"]), {"nodePathMap": [
            {"node": "DEFAULT_PATH_FOR_NON_LISTED_NODES", "paths": ["${DATA_PATH}"]},
        ]})
        # Exact no-op hook: never inherit upstream's recursive directory removal.
        self.assertEqual(config["teardown"],
                         '#!/bin/sh\nset -eu\nprintf "%s\\n" "Retaining volume directory"\n')

    def test_runtime_overrides(self):
        pod = self.resources["Deployment", "local-path-provisioner"]["spec"]["template"]["spec"]
        self.assertEqual(pod["serviceAccountName"], "local-path-provisioner-service-account")
        container, = pod["containers"]
        self.assertNotIn("--debug", container["command"])
        self.assertEqual(container["resources"], {
            "requests": {"cpu": "10m", "memory": "32Mi"}, "limits": {"memory": "128Mi"},
        })
        for name in ("startupProbe", "livenessProbe", "readinessProbe"):
            self.assertEqual(container[name]["httpGet"]["port"], "health")
        helper = self.resources["ConfigMap", "local-path-config"]["data"]["helperPod.yaml"]
        self.assertRegex(helper, r"image: docker\.io/library/busybox:[^\s]+\n")


if __name__ == "__main__":
    unittest.main()
