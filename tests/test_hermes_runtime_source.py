import base64
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


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("runtime_source", ROOT / "scripts/prepare-hermes-runtime.py")
assert spec and spec.loader
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
RUNTIME_ID = "a" * 26


def source_fixture():
    fields = [{"id": key, "label": old_label, "type": kind,
               "section": {"id": "add more"}, "value": label + '\r\n"\\\u0000\u00e9'}
              for key, (label, kind, old_label) in helper.FIELDS.items()]
    fields.extend([{"id": helper.BROAD, "value": "DO_NOT_COPY_BROAD_TOKEN"},
                   {"id": "notesPlain", "value": "DO_NOT_COPY_PRIVATE_NOTES"}])
    return {"id": helper.SOURCE, "title": "Hermes Agent", "vault": {"id": helper.VAULT},
            "sections": [{"id": "add more"}], "fields": fields}


def runtime_fixture(source):
    return {**helper.source_template(source), "id": RUNTIME_ID, "vault": {"id": helper.VAULT}}


class RuntimeSourceTests(unittest.TestCase):
    def run_main(self, responses, args=()):
        with patch.dict(os.environ, {}, clear=True), patch.object(helper, "run", side_effect=responses) as calls, \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            status = helper.main(list(args))
        text = out.getvalue() + err.getvalue()
        self.assertNotIn("DO_NOT_COPY", text)
        self.assertNotIn("PRIVATE", text)
        return status, text, calls

    def test_constructs_only_six_explicit_fields_and_empty_notes(self):
        source = source_fixture()
        template = helper.source_template(source)
        self.assertEqual(len(template["fields"]), 7)
        self.assertNotIn("DO_NOT_COPY", json.dumps(template))
        self.assertNotIn(helper.BROAD, json.dumps(template))
        self.assertNotIn("sections", template)
        helper.verify_runtime(runtime_fixture(source), template)
        for field in template["fields"][:-1]:
            original = next(f for f in source["fields"] if f["id"] == field["id"])
            self.assertEqual(field["value"].encode(), original["value"].encode())

    def test_default_only_previews_create_with_values_on_stdin(self):
        source = source_fixture()
        status, output, calls = self.run_main([source, [], runtime_fixture(source)])
        self.assertEqual(status, 0)
        self.assertIn("no writes", output)
        arguments, payload = calls.call_args.args
        self.assertIn("--dry-run", arguments)
        self.assertNotIn("value", " ".join(arguments))
        self.assertNotIn(b"DO_NOT_COPY", payload)
        self.assertEqual(json.loads(payload), helper.source_template(source))

    def test_apply_reads_back_created_item_and_source_without_editing_either(self):
        source = source_fixture()
        runtime = runtime_fixture(source)
        status, output, calls = self.run_main([source, [], runtime, runtime, source], ["--apply"])
        self.assertEqual(status, 0)
        self.assertIn("PASS hermes-runtime", output)
        self.assertEqual(sum(c.args[0][:2] == ["item", "create"] for c in calls.call_args_list), 1)
        self.assertNotIn("--dry-run", calls.call_args_list[2].args[0])
        self.assertEqual(calls.call_args_list[3].args[0][2], RUNTIME_ID)
        self.assertTrue(all("edit" not in c.args[0] for c in calls.call_args_list))

    def test_existing_runtime_is_verified_without_duplicate_create(self):
        source = source_fixture()
        runtime = runtime_fixture(source)
        inventory = [{"id": RUNTIME_ID, "title": helper.TITLE}]
        status, output, calls = self.run_main([source, inventory, runtime, runtime, source], ["--apply"])
        self.assertEqual(status, 0)
        self.assertTrue(all("create" not in c.args[0] for c in calls.call_args_list))

    def test_unexpected_runtime_fields_or_byte_changes_are_rejected(self):
        source = source_fixture()
        template = helper.source_template(source)
        for mutate in (
            lambda item: item["fields"].append({"id": helper.BROAD, "value": "PRIVATE"}),
            lambda item: item["fields"][0].update(value="PRIVATE"),
            lambda item: item["fields"][0].update(purpose="PASSWORD"),
            lambda item: item.update(files=[{"name": "PRIVATE"}]),
            lambda item: item.update(urls=[{"href": "PRIVATE"}]),
        ):
            runtime = runtime_fixture(source)
            mutate(runtime)
            with self.assertRaises(helper.Blocked):
                helper.verify_runtime(runtime, template)

    def test_failed_create_is_unknown_and_never_retried(self):
        source = source_fixture()
        failure = subprocess.CalledProcessError(1, ["op"], output=b"PRIVATE", stderr=b"PRIVATE")
        status, output, calls = self.run_main([source, [], failure], ["--apply"])
        self.assertEqual(status, 1)
        self.assertIn("stage=create category=execution-or-response-error outcome=unknown-create-inspect-before-retry", output)
        self.assertEqual(calls.call_count, 3)
        status, output, _ = self.run_main([failure], ["--apply"])
        self.assertIn("outcome=no-writes", output)

    def test_environment_overrides_and_missing_runtime_fail_without_creation(self):
        with patch.dict(os.environ, {"OP_CONNECT_TOKEN": "PRIVATE"}), patch.object(helper, "run") as calls, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(helper.main(["--apply"]), 1)
            calls.assert_not_called()
        status, output, calls = self.run_main([source_fixture(), []], ["--verify-consumers"])
        self.assertEqual(status, 1)
        self.assertIn("stage=runtime-discovery category=runtime-item-missing outcome=no-writes", output)
        self.assertEqual(calls.call_count, 2)

    def test_complete_n8n_json_and_retained_consumer_parity(self):
        template = helper.source_template(source_fixture())
        fields = {f["label"]: f["value"].encode() for f in template["fields"]}
        webhook = b'webhook\r\n"\\'
        credentials = [
            {"id": "hermesWebhookTok", "name": "Hermes webhook", "type": "httpHeaderAuth",
             "data": {"name": "X-Gitlab-Token", "value": webhook.decode()}},
            {"id": "perplexityDecider", "name": "Perplexity Decider", "type": "perplexityApi",
             "data": {"apiKey": fields["perplexity-api-key"].decode()}},
        ]
        secrets = {
            "hermes-discord": {"DISCORD_BOT_TOKEN": fields["discord-bot-token"],
                               "DISCORD_ALLOWED_USERS": fields["discord-allowed-users"],
                               "DISCORD_HOME_CHANNEL": fields["discord-home-channel"]},
            "hermes-web": {"EXA_API_KEY": fields["exa-api-key"]},
            "hermes-browser": {"BROWSER_USE_API_KEY": fields["browser-use-api-key"]},
            "hermes-webhook": {"WEBHOOK_SECRET": webhook},
            "n8n-hermes-credentials": {"credentials.json": json.dumps(credentials).encode()},
        }
        with patch.object(helper, "read_secret", side_effect=secrets.__getitem__), \
                patch.object(helper.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, webhook)), \
                contextlib.redirect_stdout(io.StringIO()):
            helper.verify_consumers(template)
            secrets["n8n-hermes-credentials"]["credentials.json"] = b'[]'
            with self.assertRaises(helper.Blocked):
                helper.verify_consumers(template)
            secrets["hermes-web"]["extra"] = b"unexpected"
            with self.assertRaises(helper.Blocked):
                helper.verify_consumers(template)

    def test_node_renderer_preserves_full_import_contract_and_rejects_empty_inputs(self):
        script = ROOT / "kubernetes/apps/n8n/render-credentials.cjs"
        code = "const {render}=require(process.argv[1]);const fs=require('node:fs');process.stdout.write(render(...JSON.parse(fs.readFileSync(0,'utf8'))));"
        inputs = ['webhook\r\n"\\\u0000\u00e9${DOMAIN}', 'perplexity\r\n"\\\u0000\u00e9{{literal}}']
        result = subprocess.run(["node", "-e", code, str(script)], input=json.dumps(inputs).encode(), capture_output=True, check=True)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed, [
            {"id": "hermesWebhookTok", "name": "Hermes webhook", "type": "httpHeaderAuth",
             "data": {"name": "X-Gitlab-Token", "value": inputs[0]}},
            {"id": "perplexityDecider", "name": "Perplexity Decider", "type": "perplexityApi",
             "data": {"apiKey": inputs[1]}},
        ])
        for inputs in (["", "key"], ["token", ""], [None, "key"], ["token", {}]):
            result = subprocess.run(["node", "-e", code, str(script)], input=json.dumps(inputs).encode(), capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(result.stdout)


if __name__ == "__main__":
    unittest.main()
