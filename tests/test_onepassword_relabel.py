import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "relabel", Path(__file__).parents[1] / "scripts/relabel-onepassword-sources.py"
)
assert spec is not None and spec.loader is not None
relabel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relabel)


class RelabelTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(relabel.os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.item_id, (self.title, self.labels) = next(iter(relabel.SOURCES.items()))
        self.original = {
            "id": self.item_id, "title": self.title, "vault": {"id": relabel.VAULT},
            "version": 1, "category": "API_CREDENTIAL", "tags": ["existing"],
            "urls": [{"label": "existing", "href": "https://example.invalid"}],
            "sections": [{"id": "existing", "label": "existing"}],
            "fields": [{"id": field_id, "label": old, "type": "CONCEALED",
                        "value": 'test-only-\n"\\한글', "section": {"id": "existing"},
                        "reference": "op://test/old"}
                       for field_id, (old, _) in self.labels.items()],
        }

    def updated(self, original=None):
        return relabel.relabel(original or self.original, self.item_id, self.title, self.labels)

    def test_only_labels_change_and_rerun_is_idempotent(self):
        updated = self.updated()
        self.assertEqual(self.updated(updated), updated)
        for original, field in zip(self.original["fields"], updated["fields"]):
            self.assertEqual(field["label"], self.labels[field["id"]][1])
            field["label"] = original["label"]
        self.assertEqual(updated, self.original)

    def test_drift_identity_attachments_and_missing_fields_fail_closed(self):
        for mutate in (
            lambda x: x.update(id="wrong"),
            lambda x: x["vault"].update(id="wrong"),
            lambda x: x.update(title="wrong"),
            lambda x: x.update(files=[{"id": "attachment"}]),
            lambda x: x["fields"][0].update(label="unexpected"),
            lambda x: x.update(fields=[]),
            lambda x: x["fields"].append(copy.deepcopy(x["fields"][0])),
        ):
            original = copy.deepcopy(self.original)
            mutate(original)
            with self.assertRaises((ValueError, KeyError)):
                self.updated(original)

    def test_values_and_field_metadata_are_checked_but_derived_references_can_change(self):
        changed = copy.deepcopy(self.original)
        changed["fields"][0]["reference"] = "op://test/new"
        self.assertEqual(relabel.content(changed), relabel.content(self.original))
        for key in ("value", "type", "id", "section"):
            changed = copy.deepcopy(self.original)
            changed["fields"][0][key] = "changed"
            self.assertNotEqual(relabel.content(changed), relabel.content(self.original))

    def test_cli_secrets_stay_in_stdin_and_failures_are_sanitized(self):
        with patch.object(relabel.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(self.original).encode())) as run:
            relabel.op("item", "edit", self.item_id, item=self.original)
        self.assertNotIn(self.original["fields"][0]["value"], repr(run.call_args.args))
        self.assertEqual(json.loads(run.call_args.kwargs["input"]), self.original)
        self.assertTrue(run.call_args.kwargs["capture_output"])
        out = io.StringIO()
        with patch.object(relabel, "op", side_effect=ValueError("PRIVATE_VALUE")):
            with contextlib.redirect_stderr(out):
                self.assertEqual(relabel.main([]), 1)
        self.assertNotIn("PRIVATE_VALUE", out.getvalue())

    def run_cli(self, responses, items=()):
        def response(value):
            if isinstance(value, BaseException):
                return value
            return subprocess.CompletedProcess([], 0, stdout=json.dumps(value).encode("utf-8"))

        out, err = io.StringIO(), io.StringIO()
        with patch.object(relabel, "SOURCES", {self.item_id: (self.title, self.labels)}), \
                patch.object(relabel.subprocess, "run", side_effect=[response(v) for v in responses]) as run, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = relabel.main(list(items))
        return code, out.getvalue() + err.getvalue(), run.call_args_list

    def test_subprocess_failures_identify_stage_without_leaking_output(self):
        secret = "PRIVATE_VALUE_\u001b[31m\nFAIL forged"
        for failure, expected in (
            (FileNotFoundError(secret), "op-not-found"),
            (PermissionError(secret), "local-process-error"),
            (subprocess.CalledProcessError(7, [secret], output=secret.encode(),
                                          stderr=f"HTTP 401 not signed in {secret}".encode()),
             "exit=7; HTTP 401; check account sign-in"),
            (subprocess.CalledProcessError(1, [secret], stderr=b"HTTP 403 forbidden"),
             "HTTP 403; check vault read/edit permissions"),
            (subprocess.CalledProcessError(2, [secret], stderr=secret.encode()),
             "op-command-failed; exit=2"),
        ):
            with self.subTest(expected=expected):
                code, output, calls = self.run_cli([failure])
                self.assertEqual(code, 1)
                self.assertIn("item=Cloudflare DNS", output)
                self.assertIn("stage=source-get", output)
                self.assertIn(expected, output)
                self.assertIn("No items written; no write commands attempted", output)
                self.assertNotIn(secret, output)
                self.assertNotIn("Earlier PASS applied", output)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0].kwargs["stdin"], subprocess.DEVNULL)

    def test_invalid_json_and_encoding_are_sanitized(self):
        for payload in (b'{"secret":"PRIVATE_VALUE"', b"\xffPRIVATE_VALUE"):
            with patch.object(relabel.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, stdout=payload)):
                out = io.StringIO()
                with contextlib.redirect_stderr(out):
                    self.assertEqual(relabel.main([]), 1)
                self.assertIn("stage=source-get: invalid-cli-json; exit=0", out.getvalue())
                self.assertNotIn("PRIVATE_VALUE", out.getvalue())

    def test_connect_presence_blocks_without_reading_or_changing_credentials(self):
        for name in ("OP_CONNECT_HOST", "OP_CONNECT_TOKEN"):
            with patch.dict(relabel.os.environ, {name: "PRIVATE_VALUE"}), \
                    patch.object(relabel.subprocess, "run") as run:
                out = io.StringIO()
                with contextlib.redirect_stderr(out):
                    self.assertEqual(relabel.main([]), 1)
                run.assert_not_called()
                self.assertIn("stage=environment: connect-environment", out.getvalue())
                self.assertIn("No items written", out.getvalue())
                self.assertNotIn("PRIVATE_VALUE", out.getvalue())
                self.assertEqual(relabel.os.environ[name], "PRIVATE_VALUE")

    def test_subprocess_dry_run_checks_utf8_stdin_and_never_writes(self):
        code, output, calls = self.run_cli([self.original, self.updated()])
        self.assertEqual(code, 0)
        self.assertIn("READ ONLY: no items written", output)
        edit = calls[1]
        self.assertIn("--dry-run", edit.args[0])
        self.assertEqual(json.loads(edit.kwargs["input"].decode("utf-8")), self.updated())
        self.assertNotIn(self.original["fields"][0]["value"], repr(edit.args))
        self.assertNotIn(self.original["fields"][0]["value"], output)

    def test_drift_and_preview_failures_are_read_only(self):
        drift = copy.deepcopy(self.original)
        drift["fields"][0]["label"] = "PRIVATE_VALUE"
        for responses, stage in (
            ([drift], "source-validation: source-drift"),
            ([self.original, []], "preview-comparison: preview-mismatch"),
            ([self.original, self.original], "preview-comparison: preview-mismatch"),
            ([self.original, subprocess.CalledProcessError(1, [], stderr=b"PRIVATE_VALUE")],
             "preview-edit: op-command-failed"),
        ):
            code, output, calls = self.run_cli(responses)
            self.assertEqual(code, 1)
            self.assertIn(stage, output)
            self.assertIn("No items written; no write commands attempted", output)
            self.assertNotIn("PRIVATE_VALUE", output)
            self.assertTrue(all("--dry-run" in c.args[0] for c in calls
                                if c.args[0][1:3] == ["item", "edit"]))

    def test_comparison_keeps_existing_metadata_exclusions_and_field_order(self):
        expected = self.updated()
        expected["fields"].append({"id": "other", "label": "other", "value": "unchanged"})
        actual = copy.deepcopy(expected)
        actual.update(version=2, updated_at="changed", tags=expected["tags"])
        actual["fields"].reverse()
        actual["fields"][1]["reference"] = "derived-reference-changed"
        relabel.check_content(expected, actual)

    def test_structural_diff_rejects_real_changes_without_exposing_values(self):
        for mutate, diagnostic in (
            (lambda x: x["fields"][0].update(value="PRIVATE_VALUE"), ".value: changed type=string"),
            (lambda x: x["fields"][0].update(value=None), ".value: type expected=string actual=null"),
            (lambda x: x["fields"][0].pop("value"), ".value: presence expected=present actual=missing"),
            (lambda x: x["fields"][0].update(id="PRIVATE_VALUE"), ".fields.property[0]: presence"),
            (lambda x: x["urls"][0].update(href="PRIVATE_VALUE"), ".urls[0].href: changed"),
            (lambda x: x.update(tags=[]), ".tags[0]: presence"),
            (lambda x: x.update(sections=[]), ".sections[0]: presence"),
            (lambda x: x.update(files=[{"name": "PRIVATE_VALUE"}]), ".files[0]: presence"),
            (lambda x: x.pop("vault"), ".vault: presence"),
            (lambda x: x["fields"].append(copy.deepcopy(x["fields"][0])), "invalid item shape"),
        ):
            with self.subTest(diagnostic=diagnostic):
                actual = self.updated()
                mutate(actual)
                code, output, calls = self.run_cli([self.original, actual])
                self.assertEqual(code, 1)
                self.assertIn(diagnostic, output)
                self.assertIn("No items written; no write commands attempted", output)
                self.assertNotIn("PRIVATE_VALUE", output)
                self.assertNotIn(self.original["fields"][0]["value"], output)
                self.assertEqual(len(calls), 2)
                self.assertIn("--dry-run", calls[1].args[0])

    def test_adversarial_dynamic_names_never_appear_in_bounded_diagnostics(self):
        secret = "PRIVATE_VALUE_\u001b[31m\nFAIL forged"
        original = copy.deepcopy(self.original)
        original["fields"].append({"id": secret, "label": secret, "value": secret,
                                   secret: {secret: secret}})
        actual = self.updated(original)
        actual["fields"][-1].update(value="changed")
        actual["fields"][-1][secret][secret] = None
        actual["fields"][0][secret] = [secret] * 30
        code, output, _ = self.run_cli([original, actual])
        self.assertEqual(code, 1)
        self.assertIn(".fields.property[", output)
        self.assertNotIn(secret, output)
        self.assertNotIn("PRIVATE_VALUE", output)
        self.assertNotIn("FAIL forged", output)
        mismatch = relabel.ContentMismatch([secret] * 30, [], "compared content")
        self.assertEqual(len(mismatch.diagnostics), 20)

    def test_string_presence_diagnostics_classify_empty_without_values(self):
        for value, marker in (("", "empty"), ("PRIVATE_VALUE", "nonempty")):
            mismatch = relabel.ContentMismatch({}, {"value": value}, "compared content")
            self.assertIn(f"type=string string={marker}", mismatch.diagnostics[0])
            self.assertNotIn("PRIVATE_VALUE", str(mismatch.diagnostics))

    def test_apply_is_disabled_before_any_cli_call_even_with_legacy_confirmation(self):
        for args in (["--apply"], ["--apply", "--confirm-no-passkeys"]):
            out = io.StringIO()
            with contextlib.redirect_stderr(out), patch.object(relabel, "op") as op:
                with self.assertRaises(SystemExit) as error:
                    relabel.main(args)
            self.assertEqual(error.exception.code, 2)
            op.assert_not_called()
            self.assertIn("automated writes disabled", out.getvalue())

    def test_absent_dates_reject_every_added_value_and_never_write(self):
        for field_id in ("validFrom", "expires"):
            for value in ("", "PRIVATE_VALUE", " ", "0", None, 0, False):
                with self.subTest(field_id=field_id, value_type=type(value).__name__):
                    original = copy.deepcopy(self.original)
                    original["fields"].append({"id": field_id, "type": "DATE", "label": field_id})
                    actual = self.updated(original)
                    actual["fields"][-1]["value"] = value
                    code, output, calls = self.run_cli([original, actual])
                    self.assertEqual(code, 1)
                    self.assertIn("preview-comparison", output)
                    self.assertIn("No items written; no write commands attempted", output)
                    self.assertNotIn("PRIVATE_VALUE", output)
                    self.assertEqual(len(calls), 2)
                    self.assertIn("--dry-run", calls[1].args[0])
                    self.assertEqual(json.loads(calls[1].kwargs["input"]), self.updated(original))

    def test_already_renamed_items_are_read_only_gets(self):
        code, output, calls = self.run_cli([self.updated()])
        self.assertEqual(code, 0, output)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0][1:3], ["item", "get"])
        self.assertIn("READ ONLY", output)

    def test_only_selected_cutover_items_are_read(self):
        out = io.StringIO()
        with patch.object(relabel, "op", return_value=self.updated()) as op, \
                contextlib.redirect_stdout(out):
            self.assertEqual(relabel.main([self.title]), 0)
        op.assert_called_once_with("item", "get", self.item_id, "--vault", relabel.VAULT)
        self.assertIn("PASS labels Cloudflare DNS", out.getvalue())
        with patch.object(relabel, "op") as op, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                relabel.main(["unreviewed"])
        op.assert_not_called()


if __name__ == "__main__":
    unittest.main()
