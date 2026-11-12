"""Protect the staged coding-route cutover without importing the Hermes runtime."""

import ast
import copy
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "kubernetes/apps/hermes/configure_profiles.py"
module = ast.parse(SOURCE.read_text())
route = next(node for node in module.body
             if isinstance(node, ast.FunctionDef) and node.name == "configure_coding_route")
namespace = {}
exec(compile(ast.Module(body=[route], type_ignores=[]), str(SOURCE), "exec"), namespace)
configure_coding_route = namespace["configure_coding_route"]


class CodingRouteTests(unittest.TestCase):
    def test_staging_keeps_native_route_and_unrelated_settings(self):
        config = {"plugins": {"enabled": ["langfuse"]},
                  "delegation": {"max_iterations": 123}, "model": {"default": "existing"}}
        configure_coding_route(config, t3_enabled=False)
        configure_coding_route(config, t3_enabled=False)
        self.assertEqual(config["plugins"]["enabled"], ["langfuse", "advanced-delegation"])
        self.assertEqual(config["delegation"]["backend"], "profile_routes")
        self.assertEqual(config["delegation"]["max_iterations"], 123)
        self.assertEqual(config["model"], {"default": "existing"})

    def test_cutover_disables_only_native_plugin_and_retains_recovery_state(self):
        config = {"plugins": {"enabled": ["langfuse", "advanced-delegation"],
                              "entries": {"advanced-delegation": {"settings": {"keep": True}}}},
                  "delegation": {"backend": "profile_routes", "historical": "keep"},
                  "mcp_servers": {"other": {"enabled": True}}}
        before = copy.deepcopy(config)
        configure_coding_route(config, t3_enabled=True)
        configure_coding_route(config, t3_enabled=True)
        self.assertEqual(config["plugins"]["enabled"], ["langfuse"])
        self.assertEqual(config["plugins"]["entries"], before["plugins"]["entries"])
        self.assertEqual(config["delegation"], before["delegation"])
        self.assertEqual(config["mcp_servers"], before["mcp_servers"])
        configure_coding_route(config, t3_enabled=False)
        self.assertIn("advanced-delegation", config["plugins"]["enabled"])
        self.assertEqual(config["delegation"]["historical"], "keep")


if __name__ == "__main__":
    unittest.main()
