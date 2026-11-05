import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

import prepare_langfuse


class PrepareLangfuseTests(unittest.TestCase):
    def test_configuration_preserves_provider_logs_and_other_plugins(self):
        config = {
            "model": {"provider": "openai-codex", "default": "gpt-6.1-sol"},
            "display": {"tool_progress": "all"},
            "plugins": {
                "enabled": ["existing-plugin"],
                "disabled": ["langfuse", "unrelated-disabled-plugin"],
                "entries": {"langfuse": {"settings": {"other_setting": "keep"}}, "existing-plugin": {"settings": {"enabled": True}}},
            },
        }
        prepare_langfuse.merge_configuration(config)
        prepare_langfuse.merge_configuration(config)
        self.assertEqual(config["model"], {"provider": "openai-codex", "default": "gpt-6.1-sol"})
        self.assertEqual(config["display"], {"tool_progress": "all"})
        self.assertEqual(config["plugins"]["enabled"], ["existing-plugin", "langfuse"])
        self.assertEqual(config["plugins"]["disabled"], ["unrelated-disabled-plugin"])
        self.assertEqual(config["plugins"]["entries"]["langfuse"]["settings"], {"other_setting": "keep", "end_user_attribution": False})
        self.assertEqual(config["plugins"]["entries"]["existing-plugin"], {"settings": {"enabled": True}})

    def test_config_save_failure_disables_staged_plugin_without_failing_bootstrap(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "plugin/plugin.yaml"
            manifest.parent.mkdir()
            manifest.write_text("synthetic staged plugin")
            with patch.object(prepare_langfuse, "prepare", return_value=True):
                with patch.object(prepare_langfuse, "configure", side_effect=OSError("read-only config fixture")):
                    prepare_langfuse.main(directory)
            self.assertFalse(manifest.exists())

    def test_network_failure_leaves_plugin_disabled_and_other_data_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            user_config = root / "config.yaml"
            user_config.write_text("existing authentication settings")
            runtime = root / "runtime"
            (runtime / "plugin").mkdir(parents=True)
            (runtime / "plugin/plugin.yaml").write_text("old plugin")
            with patch.object(prepare_langfuse, "download", side_effect=urllib.error.URLError("offline")):
                self.assertFalse(prepare_langfuse.prepare(runtime))
            self.assertFalse((runtime / "plugin/plugin.yaml").exists())
            self.assertEqual(user_config.read_text(), "existing authentication settings")

    def test_dependency_failure_never_exposes_the_plugin_manifest(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as wheel:
            wheel.writestr("sdk_fixture.py", "# synthetic fixture")

        def fixture_download(url, checksum):
            return archive.getvalue() if url.endswith(".whl") else b"# synthetic plugin fixture"

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(prepare_langfuse, "download", side_effect=fixture_download):
                with patch.object(prepare_langfuse.subprocess, "run", side_effect=subprocess.TimeoutExpired("import", 20)):
                    self.assertFalse(prepare_langfuse.prepare(directory))
            self.assertFalse((Path(directory) / "plugin/plugin.yaml").exists())

    def test_checksum_failure_refuses_downloaded_code(self):
        with patch.object(prepare_langfuse.urllib.request, "urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = b"changed source"
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                prepare_langfuse.download("https://example.invalid/fixture", "0" * 64)


if __name__ == "__main__":
    unittest.main()
