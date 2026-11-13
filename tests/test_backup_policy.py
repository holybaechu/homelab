"""Offline safety checks; these do not establish actual cloud authorization."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
RUNTIME_ID = "11111111-1111-4111-8111-111111111111"
ADMIN = "user_id:22222222-2222-4222-8222-222222222222"


class BackupPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(subprocess.check_output([
            "yq", "-p", "hcl", "-o", "json", ".", str(ROOT / "infra/backup/main.tf")
        ], text=True))
        cls.policy = cls.config["resource"]["scaleway_object_bucket_policy"]["backup"]

    def evaluate(self, expression, admins=None, scaleway=True, runtime=True):
        values = {
            "scaleway_object_bucket.backup[0].name": '"fixture-bucket"',
            "scaleway_iam_application.runtime[0].id": json.dumps(RUNTIME_ID),
            "var.scaleway_admin_principals": json.dumps([ADMIN] if admins is None else admins),
            "var.provision_scaleway": str(scaleway).lower(),
            "var.provision_runtime_credentials": str(runtime).lower(),
        }
        # Interpolations need expressions, not quoted literals inside ${...}.
        for reference, value in values.items():
            expression = expression.replace("${" + reference + "}", json.loads(value) if value.startswith('"') else value)
            expression = expression.replace(reference, value)
        with tempfile.TemporaryDirectory() as fixture:
            (Path(fixture) / "main.tf").write_text("locals {\n  result = " + expression + "\n}\n")
            output = subprocess.check_output(
                ["tofu", "-chdir=" + fixture, "console"], cwd=ROOT,
                env={**os.environ, "TF_DATA_DIR": str(Path(fixture) / "providers")},
                input="jsonencode(local.result)\n", text=True,
            )
        return json.loads(json.loads(output))

    def test_runtime_allowlist_and_explicit_recovery(self):
        policy = self.evaluate("jsondecode(" + self.policy["policy"] + ")")
        self.assertEqual(policy["Version"], "2023-04-17")
        bucket_actions = {"s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"}
        object_actions = {"s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"}
        observed = set()
        for statement in policy["Statement"]:
            self.assertEqual(statement["Effect"], "Allow")
            if statement["Principal"] == {"SCW": [ADMIN]}:
                self.assertEqual(statement["Action"], ["s3:*"])
                self.assertEqual(set(statement["Resource"]), {"fixture-bucket", "fixture-bucket/*"})
                observed.add("admin")
            else:
                self.assertEqual(statement["Principal"], {"SCW": "application_id:" + RUNTIME_ID})
                if statement["Resource"] == ["fixture-bucket"]:
                    self.assertEqual(set(statement["Action"]), bucket_actions)
                    observed.add("bucket")
                else:
                    self.assertEqual(statement["Resource"], ["fixture-bucket/*"])
                    self.assertEqual(set(statement["Action"]), object_actions)
                    observed.add("objects")
        self.assertEqual(observed, {"admin", "bucket", "objects"})

    def test_principal_validation_and_inactive_gates(self):
        validation = self.config["variable"]["scaleway_admin_principals"]["validation"]["condition"]
        separate = self.policy["lifecycle"]["precondition"]["condition"]
        for admins in ([], ["*"], ["user_id:not-a-uuid"], ["project_id:" + RUNTIME_ID]):
            self.assertFalse(self.evaluate(validation, admins=admins))
        for admins in ([ADMIN], ["application_id:" + RUNTIME_ID]):
            self.assertTrue(self.evaluate(validation, admins=admins))
        self.assertFalse(self.evaluate(separate, admins=["application_id:" + RUNTIME_ID]))
        self.assertTrue(self.evaluate(separate))
        key = self.config["resource"]["scaleway_iam_api_key"]["runtime"]
        for scaleway, runtime in ((False, False), (True, False), (False, True)):
            for resource in (self.policy, key):
                self.assertEqual(self.evaluate(resource["count"], scaleway=scaleway, runtime=runtime), 0)
            self.assertTrue(self.evaluate(validation, admins=[], scaleway=scaleway, runtime=runtime))
        self.assertEqual(self.evaluate(self.policy["count"]), 1)
        self.assertTrue({"scaleway_iam_policy.runtime", "scaleway_object_bucket_acl.backup", "scaleway_object_bucket_policy.backup"}.issubset(key["depends_on"]))
        self.assertEqual(self.policy["project_id"], "scaleway_account_project.backup[0].id")
        self.assertEqual(self.policy["bucket"], "scaleway_object_bucket.backup[0].id")


if __name__ == "__main__":
    unittest.main()
