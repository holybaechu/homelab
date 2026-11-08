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
        with self.assertRaisesRegex(ValueError, "eso-replacement-bytes"):
            self.verify()

    def test_unrelated_key_is_rejected(self):
        self.new["data"]["OP_CONNECT_TOKEN"] = base64.b64encode(b"test-only").decode()
        with self.assertRaisesRegex(ValueError, "replacement-keys"):
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
        with self.assertRaisesRegex(ValueError, "operator-not-ready"):
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
                with self.assertRaisesRegex(ValueError, "item-path"):
                    staging.verify(item, new, sources, contract)

    def source_fixture(self, contract) -> tuple[dict, dict]:
        item_id = contract["item_id"]
        title, labels = staging.SOURCE_LABELS[item_id]
        fields = [{"id": field_id, "label": label,
                   "type": staging.field_metadata(item_id, field_id)[0],
                   "purpose": staging.field_metadata(item_id, field_id)[1],
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
                for mapping in contract["sources"].values():
                    for new_key in mapping.values():
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

    def headscale_fixture(self) -> tuple[dict, dict, dict, dict]:
        contract = staging.CONTRACTS["headscale-oidc"]
        source, replacement = self.source_fixture(contract)
        item = copy.deepcopy(self.item)
        item["metadata"]["name"] = contract["name"]
        item["spec"]["itemPath"] = f"vaults/{staging.VAULT}/items/{contract['item_id']}"
        replacement["metadata"] = copy.deepcopy(self.new["metadata"])
        replacement["metadata"]["ownerReferences"][0]["name"] = contract["name"]
        replacement["metadata"]["annotations"]["operator.1password.io/item-path"] = item["spec"]["itemPath"]
        source["version"] = 2
        replacement["metadata"]["annotations"]["operator.1password.io/item-version"] = "2"
        old = {"data": {"client-secret": replacement["data"]["oidc-client-secret"]}}
        return item, replacement, old, source

    def run_headscale(self, item, replacement, old, source):
        resources = {"onepassworditem": item, "headscale-connect": replacement,
                     "headscale-oidc": old}
        def read(kind, name, namespace):
            return resources["onepassworditem" if kind == "onepassworditem" else name]
        with patch.object(staging, "read_resource", side_effect=read), \
                patch.object(staging, "read_source", return_value=source), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            result = staging.main(["headscale-oidc"])
        return result, out.getvalue() + err.getvalue()

    def test_mocked_headscale_diagnostics_distinguish_failure_stages(self):
        mutations = (
            (lambda i, r, o, s: i["status"].update(conditions=[]), "operator-not-ready Ready=missing"),
            (lambda i, r, o, s: i["status"]["conditions"][0].update(status="False", message="PRIVATE_VALUE"), "operator-not-ready Ready=False"),
            (lambda i, r, o, s: r["metadata"].update(ownerReferences=[]), "replacement-ownership"),
            (lambda i, r, o, s: r["data"].pop("oidc-client-secret"), "replacement-missing-keys"),
            (lambda i, r, o, s: o["data"].update({"PRIVATE_KEY": "PRIVATE_VALUE"}), "eso-keys"),
            (lambda i, r, o, s: o["data"].update({"client-secret": base64.b64encode(b"PRIVATE_VALUE").decode()}), "eso-replacement-bytes"),
            (lambda i, r, o, s: s["fields"][0].update(label="PRIVATE_KEY"), "source-label"),
            (lambda i, r, o, s: s["fields"][0].update(type="PRIVATE_VALUE"), "source-field-type"),
            (lambda i, r, o, s: s["fields"][0].update(section=None), "source-field-section"),
            (lambda i, r, o, s: s["fields"][0].update(value="PRIVATE_VALUE"), "source-replacement-bytes"),
        )
        for mutate, category in mutations:
            with self.subTest(category=category):
                values = self.headscale_fixture()
                mutate(*values)
                result, output = self.run_headscale(*values)
                self.assertEqual(result, 1)
                self.assertIn("category=" + category, output)
                self.assertNotIn("PASS", output)
                self.assertNotIn("PRIVATE", output)
        self.assertEqual(self.run_headscale(*self.headscale_fixture())[0], 0)

    def test_stale_secret_and_source_label_drift_are_reported_independently(self):
        item, replacement, old, source = self.headscale_fixture()
        replacement["data"] = {"PRIVATE_KEY": base64.b64encode(b"PRIVATE_VALUE").decode()}
        replacement["metadata"]["annotations"]["operator.1password.io/item-version"] = "1"
        source["fields"][0]["label"] = "PRIVATE_LABEL"
        result, output = self.run_headscale(item, replacement, old, source)
        self.assertEqual(result, 1)
        for text in ("category=replacement-keys", "category=source-label",
                     "unreviewed-count=1", "source-replacement-item-version=mismatch"):
            self.assertIn(text, output)
        self.assertNotIn("PRIVATE", output)

    def test_mocked_subprocess_failures_are_classified_and_continue_all_checks(self):
        for marker, category in (("(Forbidden)", "access-denied"),
                                 ("(NotFound)", "not-found"),
                                 ("(Unauthorized)", "unauthorized"),
                                 ("anything", "command-failed")):
            error = staging.subprocess.CalledProcessError(
                1, ["PRIVATE_COMMAND"], output=b"PRIVATE_STDOUT",
                stderr=(marker + " PRIVATE_STDERR").encode())
            with self.subTest(category=category), \
                    patch.object(staging.subprocess, "run", side_effect=error) as run, \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(staging.main(["headscale-oidc", "headscale-gateway"]), 1)
                output = err.getvalue()
            self.assertIn("category=" + category + " exit=1", output)
            self.assertIn("stage=read Kubernetes/identity/Secret/headscale-oidc", output)
            self.assertIn("stage=read Kubernetes/networking/Secret/headscale-gateway", output)
            self.assertIn("stage=read 1Password/Item/", output)
            self.assertNotIn("PRIVATE", output)
            self.assertEqual(run.call_count, 8)
            self.assertTrue(all(c.kwargs["capture_output"] for c in run.call_args_list))

    def test_nonobject_json_cannot_skip_verification_and_pass(self):
        for payload in (b"null", b"[]", b'"PRIVATE_VALUE"'):
            with self.subTest(payload=payload), \
                    patch.object(staging.subprocess, "run", return_value=staging.subprocess.CompletedProcess([], 0, payload)), \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(staging.main(["headscale-oidc"]), 1)
                self.assertIn("category=invalid-response-schema", err.getvalue())
                self.assertNotIn("PRIVATE", err.getvalue())

    def test_metadata_mode_reports_independent_drift_without_source_text_or_kubernetes(self):
        contract = staging.CONTRACTS["zipline-connect"]
        source, _ = self.source_fixture(contract)
        source.update(title="PRIVATE_TITLE", vault={"id": "PRIVATE_VAULT"},
                      files=[{"name": "PRIVATE_FILE"}], urls=[{"href": "PRIVATE_URL"}])
        source["sections"][0]["label"] = "PRIVATE_SECTION"
        source["fields"][2]["section"] = None
        source["fields"].append({"id": "PRIVATE_ID", "value": "PRIVATE_VALUE"})
        with patch.object(staging, "read_source", return_value=source), \
                patch.object(staging, "read_resource") as resources, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(staging.main(["--source-metadata", "zipline-connect"]), 0)
        resources.assert_not_called()
        output = out.getvalue()
        for text in ("item-match=true vault-match=false title-match=false",
                     "files-count=1 urls-count=1", "unreviewed-fields-count=1",
                     "section-index=0 id-reviewed=true label-present=true",
                     "section-match=false section-present=false section-index=none"):
            self.assertIn(text, output)
        self.assertNotIn("PRIVATE", output)
        self.assertNotIn("PASS", output)
        with self.assertRaises(staging.CheckFailure):
            staging.verify_source(source, {}, contract)

    def test_zipline_reviewed_section_and_existing_reference_remain_strict(self):
        contract = staging.CONTRACTS["zipline-connect"]
        source, replacement = self.source_fixture(contract)
        staging.verify_source(source, replacement, contract)
        for section in (None, {"id": "other"}):
            changed = copy.deepcopy(source)
            changed["fields"][2]["section"] = section
            with self.assertRaisesRegex(staging.CheckFailure, "source-field-section"):
                staging.verify_source(changed, replacement, contract)
        value = base64.b64decode(replacement["data"]["hermes-api-token"])
        for actual in (value, value + b"\n", b"", b"PRIVATE_VALUE"):
            with self.subTest(matches=actual == value), patch.object(
                staging.subprocess, "run", return_value=staging.subprocess.CompletedProcess([], 0, actual)
            ) as run:
                if actual == value:
                    staging.verify_references(replacement, contract)
                else:
                    with self.assertRaisesRegex(staging.CheckFailure, "source-reference-bytes"):
                        staging.verify_references(replacement, contract)
                self.assertEqual(run.call_args.args[0], [
                    "op", "read", "--no-newline",
                    "op://Homelab/Zipline/mq3jr7zyvy5zyff2zzpz7zrmgq"])
                self.assertTrue(run.call_args.kwargs["capture_output"])

    def test_reference_read_failure_blocks_pass_without_leaking_output(self):
        contract = staging.CONTRACTS["zipline-connect"]
        source, replacement = self.source_fixture(contract)
        error = staging.subprocess.CalledProcessError(
            1, ["PRIVATE_COMMAND"], output=b"PRIVATE_VALUE", stderr=b"PRIVATE_ERROR")
        with patch.object(staging, "read_resource", return_value=replacement), \
                patch.object(staging, "read_source", return_value=source), \
                patch.object(staging, "verify"), \
                patch.object(staging.subprocess, "run", side_effect=error), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(staging.main(["zipline-connect"]), 1)
        output = out.getvalue() + err.getvalue()
        self.assertIn("stage=verify source-reference/", output)
        self.assertIn("category=command-failed exit=1", output)
        self.assertNotIn("PRIVATE", output)
        self.assertNotIn("PASS", output)

    def test_langfuse_skipped_url_requires_strict_source_and_replacement_parity(self):
        contract = staging.CONTRACTS["langfuse-connect"]
        source, replacement = self.source_fixture(contract)
        source["urls"] = [{"label": "", "href": "PRIVATE_URL"}]
        staging.verify_source(source, replacement, contract)
        for mutate in (
            lambda s, r: s["urls"].append(copy.deepcopy(s["urls"][0])),
            lambda s, r: s["urls"][0].update(href=""),
            lambda s, r: s.update(files=[{"id": "PRIVATE_FILE"}]),
            lambda s, r: s.update(id="changed"),
            lambda s, r: s["fields"][0].update(label="changed"),
            lambda s, r: s["fields"][0].update(value="changed"),
            lambda s, r: s["fields"].append(copy.deepcopy(s["fields"][0])),
            lambda s, r: r["data"].update({"": base64.b64encode(b"PRIVATE_URL").decode()}),
            lambda s, r: r["data"].update({"PRIVATE_KEY": base64.b64encode(b"PRIVATE_URL").decode()}),
            lambda s, r: r["data"].pop("salt"),
            lambda s, r: r["data"].update(salt=base64.b64encode(b"changed\n").decode()),
        ):
            changed_source, changed_replacement = copy.deepcopy((source, replacement))
            mutate(changed_source, changed_replacement)
            with self.assertRaises(staging.CheckFailure):
                staging.verify_source(changed_source, changed_replacement, contract)
        zipline = staging.CONTRACTS["zipline-connect"]
        other_source, other_replacement = self.source_fixture(zipline)
        other_source["urls"] = source["urls"]
        with self.assertRaisesRegex(staging.CheckFailure, "source-identity-or-extra-content"):
            staging.verify_source(other_source, other_replacement, zipline)

    def test_url_diagnostics_hide_labels_and_values_and_reject_unreviewed_urls(self):
        contract = staging.CONTRACTS["langfuse-connect"]
        source, replacement = self.source_fixture(contract)
        for label, empty, collision, reviewed in (
            ("", True, False, False),
            ("PRIVATE_LABEL", False, False, False),
            (" ", True, False, False),
            ("\uc790\uaca9", True, False, False),
            ("salt", False, True, True),
            (" salt ", False, True, True),
        ):
            source["urls"] = [{"label": label, "href": "PRIVATE_URL"}]
            output = "\n".join(staging.source_metadata_summary(source, contract))
            for key, value in (("operator-key-empty", empty),
                               ("field-key-collision", collision), ("key-reviewed", reviewed)):
                self.assertIn(f"{key}={str(value).lower()}", output)
            self.assertNotIn("PRIVATE", output)
            if label == "":
                staging.verify_source(source, replacement, contract)
            else:
                with self.assertRaisesRegex(staging.CheckFailure, "source-identity-or-extra-content"):
                    staging.verify_source(source, replacement, contract)

    def test_metadata_mode_sanitizes_malformed_data_and_continues(self):
        with patch.object(staging, "read_source", return_value={"fields": ["PRIVATE"]}), \
                patch.object(staging, "read_resource") as resources, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(staging.main([
                "--source-metadata", "zipline-connect", "langfuse-connect"]), 1)
        resources.assert_not_called()
        self.assertEqual(err.getvalue().count("category=invalid-response-schema"), 2)
        self.assertNotIn("PRIVATE", err.getvalue())

    def test_reviewed_localized_website_urls_are_skipped_without_extra_keys(self):
        for name in ("authentik-connect", "adguard-connect", "qbittorrent-connect"):
            contract = staging.CONTRACTS[name]
            source, replacement = self.source_fixture(contract)
            source["urls"] = [{"label": "\uc6f9\uc0ac\uc774\ud2b8", "href": "PRIVATE_URL"}]
            staging.verify_source(source, replacement, contract)
            for label in ("", "website", "\uc6f9\uc0ac\uc774\ud2b8 other", "password"):
                changed = copy.deepcopy(source)
                changed["urls"][0]["label"] = label
                with self.assertRaisesRegex(ValueError, "source-identity-or-extra-content"):
                    staging.verify_source(changed, replacement, contract)
            source["urls"].append(copy.deepcopy(source["urls"][0]))
            with self.assertRaisesRegex(ValueError, "source-identity-or-extra-content"):
                staging.verify_source(source, replacement, contract)

    def test_selected_check_uses_its_namespace(self):
        with patch.object(staging, "read_resource", return_value={}) as read:
            with patch.object(staging, "verify"), patch.object(staging, "read_source"), \
                    patch.object(staging, "verify_source"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(staging.main(["headscale-gateway"]), 0)
        self.assertEqual(read.call_args_list[0].args,
                         ("onepassworditem", "headscale-connect", "networking"))


if __name__ == "__main__":
    unittest.main()
