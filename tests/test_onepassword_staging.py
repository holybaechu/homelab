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

    def source_fixture(self, contract):
        item_id = contract["item_id"]
        title, labels = staging.SOURCE_LABELS[item_id]
        fields = [{"id": field_id, "label": label, "type": "CONCEALED",
                   "section": {"id": section} if section else None,
                   "value": label + '\n"\\\u0000'}
                  for field_id, (label, section) in labels.items()]
        fields.append({"id": "notesPlain", "label": "notesPlain", "type": "STRING",
                       "purpose": "NOTES", "value": ""})
        source = {"id": item_id, "vault": {"id": staging.VAULT}, "title": title,
                  "fields": fields, "sections": [{"id": section} for section in
                   sorted({s for _, s in labels.values() if s})]}
        replacement = {"data": {staging.operator_key(f["label"]):
                                base64.b64encode(f["value"].encode()).decode()
                                for f in fields if f["value"]}}
        return source, replacement

    def test_operator_normalization_preserves_case_and_skips_korean_only(self):
        for label, key in (("PostgreSQL Password", "PostgreSQL-Password"),
                           ("Hermes API token", "Hermes-API-token"),
                           ("postgresql-password", "postgresql-password"),
                           ("\uc790\uaca9 \uc99d\uba85", ""),
                           ("\ud55c Hermes \uae00", "Hermes"),
                           ("root_password", "root_password")):
            self.assertEqual(staging.operator_key(label), key)

    def test_source_metadata_and_all_field_bytes_for_each_staged_target(self):
        for contract in staging.CONTRACTS.values():
            if contract["item_id"] not in staging.SOURCE_LABELS:
                continue
            with self.subTest(contract=contract["name"], namespace=contract["namespace"]):
                source, replacement = self.source_fixture(contract)
                staging.verify_source(source, replacement, contract)
                self.assertEqual(set(replacement["data"]), contract["allowed"] - {"notesPlain"})
                for old_key, new_key in contract["sources"][next(iter(contract["sources"]))].items():
                    self.assertIn(new_key, replacement["data"])
                replacement["data"][next(iter(replacement["data"]))] = base64.b64encode(b"changed").decode()
                with self.assertRaises(ValueError):
                    staging.verify_source(source, replacement, contract)

    def test_source_drift_uri_labels_and_extra_content_fail_closed(self):
        contract = staging.CONTRACTS["zipline-connect"]
        source, replacement = self.source_fixture(contract)
        for mutate in (
            lambda x: x.update(id="changed"),
            lambda x: x["fields"][0].update(id="changed"),
            lambda x: x["fields"][0].update(type="STRING"),
            lambda x: x["fields"][0].update(section=None),
            lambda x: x["fields"][0].update(value=""),
            lambda x: x["fields"][2].update(label="op://Homelab/Zipline/hermes-api-token"),
            lambda x: x["fields"].append(copy.deepcopy(x["fields"][0])),
            lambda x: x.update(files=[{"id": "attachment"}]),
            lambda x: x.update(urls=[{"label": "new"}]),
            lambda x: x["sections"][0].update(label="new"),
        ):
            changed = copy.deepcopy(source)
            mutate(changed)
            with self.assertRaises(ValueError):
                staging.verify_source(changed, replacement, contract)
        replacement["data"]["unexpected"] = base64.b64encode(b"test").decode()
        with self.assertRaises(ValueError):
            staging.verify_source(source, replacement, contract)

    def test_cloudflare_explicit_checks_block_before_reads(self):
        for name in ("cloudflare-dns", "cloudflare-ddns"):
            with patch.object(staging, "read_resource") as read, \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    staging.main([name])
                self.assertEqual(error.exception.code, 2)
                read.assert_not_called()
        self.assertFalse({"cloudflare-dns", "cloudflare-ddns"} & staging.CONTRACTS.keys())

    def test_source_read_failures_are_sanitized_and_never_write(self):
        with patch.object(staging.subprocess, "run", side_effect=ValueError("PRIVATE_VALUE")) as run, \
                patch.object(staging, "read_resource", return_value={}), \
                patch.object(staging, "verify"), contextlib.redirect_stderr(io.StringIO()) as out:
            self.assertEqual(staging.main(["zipline-connect"]), 1)
        self.assertNotIn("PRIVATE_VALUE", out.getvalue())
        self.assertEqual(run.call_args.args[0][0:3], ["op", "item", "get"])
        self.assertTrue(run.call_args.kwargs["capture_output"])

    def test_selected_check_uses_its_namespace(self):
        with patch.object(staging, "read_resource", return_value={}) as read:
            with patch.object(staging, "verify"), patch.object(staging, "read_source"), \
                    patch.object(staging, "verify_source"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(staging.main(["headscale-gateway"]), 0)
        self.assertEqual(read.call_args_list[0].args,
                         ("onepassworditem", "headscale-connect", "networking"))


if __name__ == "__main__":
    unittest.main()
