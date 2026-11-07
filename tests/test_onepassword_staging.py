import base64
import contextlib
import copy
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "staging", Path(__file__).parents[1] / "scripts/verify-onepassword-staging.py"
)
assert spec is not None and spec.loader is not None
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)


class StagingVerificationTests(unittest.TestCase):
    def setUp(self):
        self.contract = staging.CONTRACTS["mysql-connect"]
        self.item = {
            "metadata": {"name": "mysql-connect", "uid": "test-owner"},
            "spec": {"itemPath": "vaults/3ryhjdiydkomgh3oxfl5yrg5j4/items/" + self.contract["item_id"]},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        self.old = {"data": {
            k: base64.b64encode((k + "\n").encode()).decode()
            for k in self.contract["sources"]["mysql-env"]
        }}
        self.new = {
            "metadata": {
                "ownerReferences": [{"apiVersion": "onepassword.com/v1",
                                     "kind": "OnePasswordItem", "name": "mysql-connect",
                                     "uid": "test-owner"}],
                "annotations": {"operator.1password.io/item-path": self.item["spec"]["itemPath"]},
            },
            "data": {new: self.old["data"][old] for old, new in
                     self.contract["sources"]["mysql-env"].items()},
        }

    def verify(self, replacement=None):
        staging.verify(self.item, replacement or self.new,
                       {"mysql-env": self.old}, self.contract)

    def test_valid_and_optional_own_app_notes(self):
        self.new["data"]["notesPlain"] = base64.b64encode(b"own-app notes").decode()
        self.verify()

    def test_byte_change_including_newline_is_rejected(self):
        self.new["data"]["password"] = base64.b64encode(b"password").decode()
        with self.assertRaisesRegex(ValueError, "unequal credential bytes"):
            self.verify()

    def test_unrelated_key_is_rejected(self):
        self.new["data"]["OP_CONNECT_TOKEN"] = base64.b64encode(b"test-only").decode()
        with self.assertRaisesRegex(ValueError, "unexpected replacement keys"):
            self.verify()

    def test_missing_key_wrong_owner_and_not_ready_are_rejected(self):
        for mutate in (
            lambda x: x["data"].pop("password"),
            lambda x: x["metadata"]["ownerReferences"][0].update(uid="other"),
            lambda x: x["metadata"]["annotations"].clear(),
        ):
            replacement = copy.deepcopy(self.new)
            mutate(replacement)
            with self.assertRaises((KeyError, ValueError)):
                self.verify(replacement)
        self.item["status"]["conditions"][0]["status"] = "False"
        with self.assertRaisesRegex(ValueError, "not Ready"):
            self.verify()

    def test_failures_do_not_print_resource_or_exception_contents(self):
        out = io.StringIO()
        with patch.object(staging, "read_resource", side_effect=ValueError("PRIVATE_VALUE")):
            with contextlib.redirect_stderr(out):
                self.assertEqual(staging.main(["mysql-connect"]), 1)
        self.assertNotIn("PRIVATE_VALUE", out.getvalue())
        self.assertIn("FAIL mysql-connect", out.getvalue())

    def test_all_contracts_preserve_exact_bytes_and_reject_wrong_source(self):
        for contract in staging.CONTRACTS.values():
            with self.subTest(contract=contract["name"], namespace=contract["namespace"]):
                item = copy.deepcopy(self.item)
                item["spec"]["itemPath"] = (
                    "vaults/3ryhjdiydkomgh3oxfl5yrg5j4/items/" + contract["item_id"]
                )
                new = copy.deepcopy(self.new)
                new["metadata"]["annotations"]["operator.1password.io/item-path"] = item["spec"]["itemPath"]
                new["data"] = {}
                sources = {}
                for name, mapping in contract["sources"].items():
                    sources[name] = {"data": {}}
                    for old_key, new_key in mapping.items():
                        value = base64.b64encode((new_key + '\n"\\\u0000').encode()).decode()
                        sources[name]["data"][old_key] = value
                        new["data"][new_key] = value
                staging.verify(item, new, sources, contract)
                item["spec"]["itemPath"] = "vaults/wrong/items/wrong"
                new["metadata"]["annotations"]["operator.1password.io/item-path"] = item["spec"]["itemPath"]
                with self.assertRaisesRegex(ValueError, "unexpected source item"):
                    staging.verify(item, new, sources, contract)

    def test_selected_check_uses_its_namespace(self):
        with patch.object(staging, "read_resource", return_value={}) as read:
            with patch.object(staging, "verify"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(staging.main(["headscale-gateway"]), 0)
        self.assertEqual(read.call_args_list[0].args,
                         ("onepassworditem", "headscale-connect", "networking"))


if __name__ == "__main__":
    unittest.main()
