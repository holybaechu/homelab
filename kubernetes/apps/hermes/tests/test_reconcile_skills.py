"""Exercise the bootstrap CLI against synthetic skills, never the real profile."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "reconcile_skills.py"


class ReconcileSkillsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.skills = self.root / "skills"
        self.skill = self.skills / "research" / "recall" / "SKILL.md"
        self.skill.parent.mkdir(parents=True)
        self.skill.write_text(
            '---\nname: recall\ndescription: "Old trigger."\n---\n\n'
            '# Recall\n\nOriginal procedure.\n\n사용자의 기존 교정.\n',
            encoding="utf-8",
        )
        self.patches = self.root / "overrides.json"

    def write_patches(self, patches):
        self.patches.write_text(json.dumps(patches), encoding="utf-8")

    def run_cli(self):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--skills-dir", str(self.skills),
             "--patches", str(self.patches)],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )

    def test_applies_requested_edits_and_preserves_custom_content(self):
        helper = self.skill.parent / "scripts" / "custom.py"
        helper.parent.mkdir()
        helper.write_bytes(b"# user helper\n")
        other = self.skills / "other" / "SKILL.md"
        other.parent.mkdir()
        other.write_bytes(b"# untouched custom skill\n")
        self.write_patches([{
            "path": "research/recall/SKILL.md",
            "replacements": [
                {"old": 'description: "Old trigger."',
                 "new": 'description: "Recall earlier fixes."'},
                {"old": "Original procedure.", "new": "Read relevant records."},
            ],
        }])
        metadata = self.skill.stat()

        result = self.run_cli()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.skill.read_text(encoding="utf-8"),
            '---\nname: recall\ndescription: "Recall earlier fixes."\n---\n\n'
            '# Recall\n\nRead relevant records.\n\n사용자의 기존 교정.\n',
        )
        self.assertEqual(helper.read_bytes(), b"# user helper\n")
        self.assertEqual(other.read_bytes(), b"# untouched custom skill\n")
        self.assertEqual(self.skill.stat().st_mode, metadata.st_mode)
        if os.name == "posix":
            self.assertEqual(self.skill.stat().st_uid, metadata.st_uid)
            self.assertEqual(self.skill.stat().st_gid, metadata.st_gid)

    def test_rerun_does_not_duplicate_an_expanded_instruction(self):
        self.write_patches([{
            "path": "research/recall/SKILL.md",
            "replacements": [{"old": "Original procedure.",
                              "new": "Original procedure. Read relevant records."}],
        }])
        first = self.run_cli()
        self.assertEqual(first.returncode, 0, first.stderr)
        saved = self.skill.read_bytes()

        second = self.run_cli()

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.skill.read_bytes(), saved)
        self.assertIn("Updated 0 skill file(s).", second.stdout)

    def test_invalid_later_target_preserves_every_original(self):
        other = self.skills / "other" / "SKILL.md"
        other.parent.mkdir()
        original = self.skill.read_bytes()
        for body in (None, "Changed upstream.", "Repeated. Repeated.",
                     "Replacement. Repeated."):
            with self.subTest(body=body):
                if body is None:
                    other.unlink(missing_ok=True)
                else:
                    other.write_text(body, encoding="utf-8")
                self.write_patches([
                    {"path": "research/recall/SKILL.md", "replacements": [
                        {"old": "Original procedure.", "new": "New procedure."}]},
                    {"path": "other/SKILL.md", "replacements": [
                        {"old": "Repeated.", "new": "Replacement."}]},
                ])

                result = self.run_cli()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("other", result.stderr)
                self.assertEqual(self.skill.read_bytes(), original)
                if body is not None:
                    self.assertEqual(other.read_text(encoding="utf-8"), body)

    def test_publish_failure_preserves_target_and_reports_partial_updates(self):
        other = self.skills / "other" / "SKILL.md"
        other.parent.mkdir()
        other.write_bytes(b"Original second skill.\n")
        self.write_patches([
            {"path": "research/recall/SKILL.md", "replacements": [
                {"old": "Original procedure.", "new": "New procedure."}]},
            {"path": "other/SKILL.md", "replacements": [
                {"old": "Original second skill.", "new": "New second skill."}]},
        ])
        # Simulate a filesystem publication failure, without mocking the reconciler.
        wrapper = '''
import os, runpy, sys
from unittest.mock import patch
original_replace = os.replace
publishes = 0
def fail_second_publish(source, target):
    global publishes
    publishes += 1
    if publishes == 2:
        raise OSError("synthetic publication failure")
    return original_replace(source, target)
sys.argv = sys.argv[1:]
with patch("os.replace", fail_second_publish):
    runpy.run_path(sys.argv[0], run_name="__main__")
'''

        result = subprocess.run(
            [sys.executable, "-c", wrapper, str(SCRIPT),
             "--skills-dir", str(self.skills), "--patches", str(self.patches)],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("after 1 update(s)", result.stderr)
        self.assertIn("New procedure.", self.skill.read_text(encoding="utf-8"))
        self.assertEqual(other.read_bytes(), b"Original second skill.\n")
        self.assertEqual(list(other.parent.iterdir()), [other])

    def test_rejects_targets_outside_skill_files_in_selected_directory(self):
        outside = self.root / "SKILL.md"
        outside.write_bytes(b"Original procedure.\n")
        helper = self.skills / "helper.py"
        helper.write_bytes(b"Original procedure.\n")
        for target in ("../SKILL.md", str(outside), "helper.py"):
            with self.subTest(target=target):
                self.write_patches([{
                    "path": target, "replacements": [
                        {"old": "Original procedure.", "new": "New procedure."}],
                }])

                result = self.run_cli()

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(outside.read_bytes(), b"Original procedure.\n")
                self.assertEqual(helper.read_bytes(), b"Original procedure.\n")


if __name__ == "__main__":
    unittest.main()
