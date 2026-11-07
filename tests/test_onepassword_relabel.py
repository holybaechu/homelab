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

    def test_default_is_read_only_and_apply_reads_back_exact_target(self):
        for apply in (False, True):
            with self.subTest(apply=apply):
                values = [self.original, self.updated()]
                if apply:
                    values += [self.original, self.updated(), self.updated()]
                with patch.object(relabel, "SOURCES", {self.item_id: (self.title, self.labels)}):
                    with patch.object(relabel, "op", side_effect=values) as op:
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(relabel.main(
                                ["--apply", "--confirm-no-passkeys"] if apply else []), 0)
                edits = [call for call in op.call_args_list if call.args[:2] == ("item", "edit")]
                self.assertEqual(len(edits), 2 if apply else 1)
                self.assertIn("--dry-run", edits[0].args)
                if apply:
                    self.assertEqual(op.call_args_list[-1].args[:3], ("item", "get", self.item_id))

    def test_preview_mismatch_or_concurrent_edit_never_writes(self):
        for values in ([self.original, self.original],
                       [self.original, self.updated(), {**self.original, "version": 2}]):
            with patch.object(relabel, "SOURCES", {self.item_id: (self.title, self.labels)}):
                with patch.object(relabel, "op", side_effect=values) as op:
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(relabel.main(["--apply", "--confirm-no-passkeys"]), 1)
            self.assertTrue(all("--dry-run" in call.args for call in op.call_args_list
                                if call.args[:2] == ("item", "edit")))

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

    def test_apply_requires_explicit_passkey_review(self):
        with contextlib.redirect_stderr(io.StringIO()), patch.object(relabel, "op") as op:
            with self.assertRaises(SystemExit):
                relabel.main(["--apply"])
        op.assert_not_called()


if __name__ == "__main__":
    unittest.main()
