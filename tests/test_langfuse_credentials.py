"""Credential scope/removal regression checks without a Hermes installation."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "kubernetes/apps/hermes/langfuse/configure_mcp.py"


class LangfuseCredentialsTest(unittest.TestCase):
    def load(self, *, fail_write=False):
        self.values = {}
        config = types.SimpleNamespace()

        def write(key, value):
            if fail_write:
                raise OSError("protected writer failed")
            self.values[key] = value

        config.require_env_writable = lambda *_: None
        config.save_env_value = write
        config.remove_env_value = lambda key: self.values.pop(key, None)
        with patch.dict(sys.modules, {"hermes_cli": types.ModuleType("hermes_cli"),
                                     "hermes_cli.config": config}):
            spec = importlib.util.spec_from_file_location("configure_langfuse", SCRIPT)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module

    def test_keys_stay_scoped_and_either_missing_key_removes_cached_credentials(self):
        module = self.load()
        for public, secret in (("public-fixture", "secret-fixture"), ("", "secret-fixture"),
                               ("public-fixture", ""), ("", "")):
            with self.subTest(public_present=bool(public), secret_present=bool(secret)):
                self.values["HERMES_LANGFUSE_MCP_AUTH"] = "stale"
                with patch.dict("os.environ", {"HERMES_LANGFUSE_PUBLIC_KEY": public,
                                               "HERMES_LANGFUSE_SECRET_KEY": secret}):
                    module.configure()
                if public and secret:
                    self.assertEqual(self.values, {
                        "HERMES_LANGFUSE_MCP_AUTH":
                            "Basic cHVibGljLWZpeHR1cmU6c2VjcmV0LWZpeHR1cmU="})
                else:
                    self.assertEqual(self.values, {})

    def test_writer_failure_is_not_silenced(self):
        module = self.load(fail_write=True)
        with patch.dict("os.environ", {"HERMES_LANGFUSE_PUBLIC_KEY": "public-fixture",
                                       "HERMES_LANGFUSE_SECRET_KEY": "secret-fixture"}):
            with self.assertRaises(OSError):
                module.configure()
        self.assertEqual(self.values, {})


if __name__ == "__main__":
    unittest.main()
