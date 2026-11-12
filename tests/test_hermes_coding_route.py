"""Remove the retired native route without changing unrelated configuration."""

import ast
import copy
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "kubernetes/apps/hermes/configure_profiles.py"
module = ast.parse(SOURCE.read_text())
route = next(node for node in module.body
             if isinstance(node, ast.FunctionDef) and node.name == "remove_coder_delegation")
namespace = {}
exec(compile(ast.Module(body=[route], type_ignores=[]), str(SOURCE), "exec"), namespace)
remove_coder_delegation = namespace["remove_coder_delegation"]


class CodingRouteTests(unittest.TestCase):
    def test_absent_route_is_not_recreated(self):
        config = {"plugins": {"enabled": ["langfuse"]},
                  "delegation": {"max_iterations": 123}, "model": {"default": "existing"}}
        before = copy.deepcopy(config)
        remove_coder_delegation(config)
        remove_coder_delegation(config)
        self.assertEqual(config, before)

    def test_legacy_route_is_removed_even_when_t3_is_disabled(self):
        config = {"plugins": {"enabled": ["langfuse", "advanced-delegation"],
                              "entries": {"advanced-delegation": {}, "langfuse": {"keep": True}}},
                  "delegation": {"backend": "profile_routes", "inherit_mcp_toolsets": False,
                                 "backends": {"profile_routes": {"profiles": [{"name": "coder"}]}},
                                 "max_iterations": 123},
                  "mcp_servers": {"t3code": {"enabled": False}, "other": {"enabled": True}}}
        before = copy.deepcopy(config)
        remove_coder_delegation(config)
        remove_coder_delegation(config)
        self.assertEqual(config["plugins"]["enabled"], ["langfuse"])
        self.assertEqual(config["plugins"]["entries"], {"langfuse": {"keep": True}})
        self.assertEqual(config["delegation"], {"max_iterations": 123})
        self.assertEqual(config["mcp_servers"], before["mcp_servers"])

    def test_unrelated_backend_survives(self):
        config = {"delegation": {"backend": "other", "backends": {"other": {"keep": True}}}}
        remove_coder_delegation(config)
        self.assertEqual(config["delegation"],
                         {"backend": "other", "backends": {"other": {"keep": True}}})


if __name__ == "__main__":
    unittest.main()
