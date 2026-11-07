"""Guard against destructive storage defaults when upgrading the upstream base."""

import json
from pathlib import Path
import shlex
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LocalPathOverlayTest(unittest.TestCase):
    def test_storage_retention(self):
        rendered = subprocess.check_output(
            ["kustomize", "build", "kubernetes/platform/controllers/local-path"], cwd=ROOT,
        )
        documents = subprocess.check_output(
            ["yq", "-N", "-o=json", "-I=0", "."], input=rendered,
        )
        resources = {
            (doc["kind"], doc["metadata"]["name"]): doc
            for doc in map(json.loads, documents.splitlines())
        }
        self.assertEqual(resources["StorageClass", "local-path"]["reclaimPolicy"], "Retain")
        config = resources["ConfigMap", "local-path-config"]["data"]
        self.assertEqual(json.loads(config["config.json"])["nodePathMap"], [
            {"node": "DEFAULT_PATH_FOR_NON_LISTED_NODES", "paths": ["${DATA_PATH}"]},
        ])
        # Allow harmless shell commands, not just the absence of upstream's `rm -rf`.
        for line in config["teardown"].splitlines():
            tokens = shlex.split(line, comments=True)
            if tokens:
                self.assertIn(tokens[0], {"set", "printf", "echo", ":", "true", "exit"})
                self.assertFalse(set("$`;&|<>") & set(" ".join(tokens)),
                                 "teardown must not expand or chain shell commands")


if __name__ == "__main__":
    unittest.main()
