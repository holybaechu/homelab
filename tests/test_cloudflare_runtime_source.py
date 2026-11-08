import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "cloudflare_source", Path(__file__).parents[1] / "scripts/prepare-cloudflare-runtime.py")
assert spec and spec.loader
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
RUNTIME_ID = "a" * 26
PRIVATE = 'PRIVATE\r\n"\\\u0000\u00e9'


def source_fixture():
    fields = [{"id": key, "label": label, "type": kind,
               **({"purpose": purpose} if purpose else {}),
               **({} if kind == "DATE" else {"value": PRIVATE})}
              for key, (label, kind, purpose) in helper.SOURCE_FIELDS.items()]
    return {"id": helper.SOURCE, "title": "Cloudflare DNS", "category": "API_CREDENTIAL",
            "vault": {"id": helper.VAULT}, "fields": fields, "version": 42}


def runtime_fixture(source):
    return {**helper.source_template(source), "id": RUNTIME_ID, "vault": {"id": helper.VAULT}}


class CloudflareSourceTests(unittest.TestCase):
    def invoke(self, responses=(), args=("--recovery-confirmed",), secrets=None):
        with (patch.dict(os.environ, {}, clear=True),
              patch.object(helper, "run", side_effect=responses) as calls,
              patch.object(helper, "verify_consumers", side_effect=secrets) as gates,
              contextlib.redirect_stdout(io.StringIO()) as out,
              contextlib.redirect_stderr(io.StringIO()) as err):
            status = helper.main(list(args))
        output = out.getvalue() + err.getvalue()
        self.assertNotIn("PRIVATE", output)
        return status, output, calls, gates

    def test_recovery_and_personal_session_required_before_any_reads(self):
        status, output, calls, gates = self.invoke(args=())
        self.assertEqual(status, 1)
        self.assertIn("private-recovery-not-confirmed", output)
        calls.assert_not_called()
        gates.assert_not_called()
        with (patch.dict(os.environ, {"OP_CONNECT_TOKEN": "PRIVATE"}),
              patch.object(helper, "run") as calls,
              contextlib.redirect_stderr(io.StringIO())):
            self.assertEqual(helper.main(["--recovery-confirmed", "--apply"]), 1)
            calls.assert_not_called()

    def test_constructs_only_credential_and_empty_notes_without_modifying_original(self):
        source = source_fixture()
        before = copy.deepcopy(source)
        template = helper.source_template(source)
        self.assertEqual(source, before)
        self.assertEqual([f["id"] for f in template["fields"]], ["credential", "notesPlain"])
        self.assertEqual(template["fields"][0]["value"].encode(), PRIVATE.encode())
        self.assertEqual(template["fields"][1]["value"], "")
        helper.verify_runtime(runtime_fixture(source), template)

    def test_both_dates_must_be_absent_not_empty_null_zero_or_epoch(self):
        for key in ("validFrom", "expires"):
            for value in ("", None, 0, "0", "1970-01-01", " ", 123):
                with self.subTest(key=key, value=value):
                    source = source_fixture()
                    next(f for f in source["fields"] if f["id"] == key)["value"] = value
                    status, output, calls, gates = self.invoke([source], ["--recovery-confirmed", "--apply"])
                    self.assertEqual(status, 1)
                    self.assertIn("recovered-source-date-present", output)
                    self.assertEqual(calls.call_count, 1)
                    gates.assert_not_called()

    def test_default_is_captured_stdin_preview_with_full_source_and_retained_readback(self):
        source = source_fixture()
        status, output, calls, gates = self.invoke([source, [], runtime_fixture(source), source])
        self.assertEqual(status, 0)
        self.assertIn("preview", output)
        self.assertEqual(gates.call_count, 2)
        arguments, payload = calls.call_args_list[2].args
        self.assertIn("--dry-run", arguments)
        self.assertNotIn(PRIVATE, " ".join(arguments))
        self.assertEqual(json.loads(payload), helper.source_template(source))

    def test_create_once_reads_back_exact_item_and_unchanged_source(self):
        source = source_fixture()
        runtime = runtime_fixture(source)
        status, output, calls, gates = self.invoke(
            [source, [], runtime, runtime, source], ["--recovery-confirmed", "--apply"])
        self.assertEqual(status, 0)
        self.assertIn("PASS cloudflare-runtime", output)
        self.assertEqual(gates.call_count, 2)
        self.assertEqual(sum(c.args[0][:2] == ["item", "create"] for c in calls.call_args_list), 1)
        self.assertFalse(any("edit" in c.args[0] for c in calls.call_args_list))
        changed = copy.deepcopy(source)
        changed["fields"][0]["value"] += "changed"
        status, output, _, _ = self.invoke([source, [], runtime, runtime, changed], ["--recovery-confirmed", "--apply"])
        self.assertEqual(status, 1)
        self.assertIn("original-source-changed", output)
        self.assertIn("unknown-create-inspect-before-retry", output)

    def test_existing_runtime_is_verified_never_duplicated_and_ambiguous_titles_block(self):
        source = source_fixture()
        runtime = runtime_fixture(source)
        inventory = [{"id": RUNTIME_ID, "title": helper.TITLE}]
        status, _, calls, _ = self.invoke([source, inventory, runtime, runtime, source])
        self.assertEqual(status, 0)
        self.assertFalse(any("create" in c.args[0] for c in calls.call_args_list))
        status, output, calls, _ = self.invoke([source, inventory * 2])
        self.assertEqual(status, 1)
        self.assertIn("runtime-title-ambiguous", output)
        self.assertEqual(calls.call_count, 2)

    def test_malformed_or_expanded_items_and_changed_credential_block(self):
        source = source_fixture()
        for mutate in (
            lambda x: x.update(id="bad"),
            lambda x: x["vault"].update(id="bad"),
            lambda x: x["fields"].append(x["fields"][0]),
            lambda x: x["fields"][2].update(label="credential"),
            lambda x: x.update(files=[{"name": PRIVATE}]),
            lambda x: x.update(urls=[{"href": PRIVATE}]),
        ):
            changed = copy.deepcopy(source)
            mutate(changed)
            with self.assertRaises(helper.Blocked):
                helper.source_template(changed)
        template = helper.source_template(source)
        for mutate in (
            lambda x: x["fields"][0].update(value="changed"),
            lambda x: x["fields"].append({"id": "validFrom", "type": "DATE"}),
            lambda x: x["fields"][1].update(value=PRIVATE),
            lambda x: x.update(urls=[{"href": PRIVATE}]),
        ):
            changed = runtime_fixture(source)
            mutate(changed)
            with self.assertRaises(helper.Blocked):
                helper.verify_runtime(changed, template)

    def test_failed_creation_never_retries_or_exposes_output_and_eso_failure_prevents_create(self):
        failure = subprocess.CalledProcessError(1, ["op"], output=PRIVATE.encode(), stderr=PRIVATE.encode())
        status, output, calls, _ = self.invoke([source_fixture(), [], failure], ["--recovery-confirmed", "--apply"])
        self.assertEqual(status, 1)
        self.assertIn("unknown-create-inspect-before-retry", output)
        self.assertEqual(calls.call_count, 3)
        status, output, calls, _ = self.invoke([source_fixture()], secrets=helper.Blocked("retained-eso-key-set-or-bytes"))
        self.assertEqual(status, 1)
        self.assertIn("retained-eso-parity", output)
        self.assertEqual(calls.call_count, 1)

    def test_source_diagnostic_stops_after_one_get_without_preview_or_secret_reads(self):
        status, output, calls, gates = self.invoke(
            [source_fixture()], ["--recovery-confirmed", "--diagnose-source"])
        self.assertEqual(status, 0)
        self.assertIn("creation and consumer parity not checked", output)
        calls.assert_called_once_with(["item", "get", helper.SOURCE, "--vault", helper.VAULT, "--format=json"])
        gates.assert_not_called()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            helper.main(["--recovery-confirmed", "--diagnose-source", "--apply"])

    def test_source_failures_have_fixed_diagnostics_and_never_continue(self):
        failures = [
            (subprocess.CalledProcessError(7, ["op", PRIVATE], output=PRIVATE.encode(),
                stderr=(PRIVATE + " HTTP 401 session expired locked").encode()),
             "cli-command-failed exit=7 hints=http-401,account-auth-hint,desktop-lock-or-connection-hint"),
            (subprocess.CalledProcessError(1, ["op"], stderr=(PRIVATE + " HTTP 403 permission").encode()),
             "cli-command-failed exit=1 hints=http-403,vault-access-hint"),
            (subprocess.CalledProcessError(1, ["op"], stderr=PRIVATE.encode()),
             "cli-command-failed exit=1 hints=none"),
            (subprocess.TimeoutExpired(["op", PRIVATE], 60, output=PRIVATE, stderr=PRIVATE), "cli-timeout"),
            (FileNotFoundError(PRIVATE), "cli-executable-not-found"),
            (PermissionError(PRIVATE), "cli-execution-permission-denied"),
            (OSError(PRIVATE), "cli-os-error"),
            (subprocess.CompletedProcess([], 0, b"\xff" + PRIVATE.encode()), "response-encoding-invalid"),
            (subprocess.CompletedProcess([], 0, PRIVATE.encode()), "response-invalid-json"),
            (subprocess.CompletedProcess([], 0, b"{}"), "response-schema-invalid"),
        ]
        for failure, category in failures:
            with (self.subTest(category=category), patch.dict(os.environ, {}, clear=True),
                  patch.object(helper.subprocess, "run", side_effect=[failure]) as calls,
                  patch.object(helper, "verify_consumers") as gates,
                  contextlib.redirect_stdout(io.StringIO()) as out,
                  contextlib.redirect_stderr(io.StringIO()) as err):
                self.assertEqual(helper.main(["--recovery-confirmed", "--diagnose-source"]), 1)
                output = out.getvalue() + err.getvalue()
                stage = "source-validation" if category == "response-schema-invalid" else "source-get"
                self.assertIn(f"stage={stage} category={category} outcome=no-writes", output)
                self.assertNotIn("PRIVATE", output)
                self.assertNotIn("session expired", output)
                calls.assert_called_once()
                self.assertEqual(calls.call_args.args[0][:3], ["op", "item", "get"])
                gates.assert_not_called()

    def test_retained_secret_complete_key_sets_and_bytes_in_both_namespaces(self):
        import base64
        template = helper.source_template(source_fixture())
        responses = [{"data": {key: base64.b64encode(PRIVATE.encode()).decode()}}
                     for key in ("api-token", "CLOUDFLARE_API_TOKEN")]
        for mutate in (None, "extra", "drift"):
            data = copy.deepcopy(responses)
            if mutate == "extra":
                data[1]["data"]["extra"] = ""
            if mutate == "drift":
                data[0]["data"]["api-token"] = ""
            results = [subprocess.CompletedProcess([], 0, json.dumps(x).encode()) for x in data]
            with patch.object(helper.subprocess, "run", side_effect=results) as calls:
                if mutate:
                    with self.assertRaises(helper.Blocked):
                        helper.verify_consumers(template)
                else:
                    helper.verify_consumers(template)
                    self.assertEqual([c.args[0][2] for c in calls.call_args_list], ["cert-manager", "homelab"])


if __name__ == "__main__":
    unittest.main()
