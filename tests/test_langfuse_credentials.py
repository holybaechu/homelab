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
        self.home = Path("/fixture")
        self.values = {}
        config = types.SimpleNamespace()
        constants = types.SimpleNamespace()

        def set_home(home):
            previous, self.home = self.home, home
            return previous

        def write(key, value):
            if fail_write and self.home.name == "coder":
                raise OSError("protected writer failed")
            self.values[self.home, key] = value

        config.require_env_writable = lambda *_: None
        config.save_env_value = write
        config.remove_env_value = lambda key: self.values.pop((self.home, key), None)
        constants.get_hermes_home = lambda: self.home
        constants.set_hermes_home_override = set_home
        constants.reset_hermes_home_override = set_home
        with patch.dict(sys.modules, {"hermes_cli": types.ModuleType("hermes_cli"),
                                     "hermes_cli.config": config, "hermes_constants": constants}):
            spec = importlib.util.spec_from_file_location("configure_langfuse", SCRIPT)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module

    def test_keys_stay_scoped_and_either_missing_key_removes_cached_credentials(self):
        module = self.load()
        root, coder = Path("/fixture"), Path("/fixture/profiles/coder")
        for public, secret in (("public-fixture", "secret-fixture"), ("", "secret-fixture"),
                               ("public-fixture", ""), ("", "")):
            with self.subTest(public_present=bool(public), secret_present=bool(secret)):
                for key in ("PUBLIC_KEY", "SECRET_KEY", "BASE_URL"):
                    self.values[coder, "HERMES_LANGFUSE_" + key] = "stale"
                self.values[root, "HERMES_LANGFUSE_MCP_AUTH"] = "stale"
                with patch.dict("os.environ", {"HERMES_LANGFUSE_PUBLIC_KEY": public,
                                               "HERMES_LANGFUSE_SECRET_KEY": secret}):
                    module.configure()
                self.assertEqual(self.home, root)
                if public and secret:
                    self.assertEqual(self.values[coder, "HERMES_LANGFUSE_SECRET_KEY"], secret)
                    self.assertNotIn((root, "HERMES_LANGFUSE_SECRET_KEY"), self.values)
                    self.assertEqual(self.values[coder, "HERMES_LANGFUSE_BASE_URL"],
                                     "http://langfuse.homelab.svc.cluster.local:3000")
                else:
                    self.assertFalse(any(home == coder for home, _ in self.values))
                    self.assertNotIn((root, "HERMES_LANGFUSE_MCP_AUTH"), self.values)

    def test_writer_failure_restores_profile_scope(self):
        module = self.load(fail_write=True)
        with patch.dict("os.environ", {"HERMES_LANGFUSE_PUBLIC_KEY": "public-fixture",
                                       "HERMES_LANGFUSE_SECRET_KEY": "secret-fixture"}):
            with self.assertRaises(OSError):
                module.configure()
        self.assertEqual(self.home, Path("/fixture"))


if __name__ == "__main__":
    unittest.main()
