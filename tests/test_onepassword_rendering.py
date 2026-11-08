import importlib.util
import json
import subprocess
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


immich = load("immich_render", "kubernetes/apps/immich/render.py")
adguard = load("adguard_render", "kubernetes/apps/adguard/render.py")
qbittorrent = load("qbittorrent_render", "kubernetes/apps/qbittorrent/render.py")


class RenderVerificationTests(unittest.TestCase):
    def test_immich_escaping_and_full_config_preservation(self):
        template = (ROOT / "kubernetes/apps/immich/immich.json").read_text()
        expected = json.loads(template)
        for credential in ('quotes" backslash\\\n\r\n\t\u0000\u00e9', '${DOMAIN}{{literal}}'):
            with self.subTest(credential=credential):
                output = json.loads(immich.render(template, credential))
                expected["oauth"]["clientSecret"] = credential
                self.assertEqual(output, expected)

    def test_immich_refuses_empty_or_embedded_credentials(self):
        for template, credential in (('{"oauth":{}}', ''), ('{"oauth":{"clientSecret":"x"}}', 'x'),
                                     ('{"oauth":null}', 'x')):
            with self.assertRaises(ValueError):
                immich.render(template, credential)

    def test_validated_hash_files_escape_and_reject_invalid_inputs(self):
        cases = (
            (adguard, "adguard", "AdGuardHome.yaml", "$2b$12$" + "A" * 53),
            (qbittorrent, "qbittorrent", "managed.conf", "@ByteArray(" + "A" * 22 + "==:" + "B" * 86 + "==)"),
        )
        for module, app, key, password_hash in cases:
            with self.subTest(app=app):
                template = (ROOT / f"kubernetes/apps/{app}/{key}.tmpl").read_text()
                output = module.render(template, "user-name.1", password_hash).decode()
                username_line = ('WebUI\\Username=user-name.1' if app == 'qbittorrent'
                                 else 'name: ' + json.dumps('user-name.1'))
                self.assertIn(username_line, output)
                self.assertIn(json.dumps(password_hash), output)
                self.assertNotIn("{{", output)
                for username in ("", "a\nadmin", "a\r", "a" * 65, "{{ .value }}", "a=1"):
                    with self.assertRaises(ValueError):
                        module.render(template, username, password_hash)
                for invalid_hash in ("", password_hash + "\n", "\"\nadmin", password_hash[:-1]):
                    with self.assertRaises(ValueError):
                        module.render(template, "user", invalid_hash)
                with self.assertRaisesRegex(ValueError, "unsupported template"):
                    module.render(template + "{{ .unreviewed }}", "user", password_hash)

    def test_node_renderer_preserves_full_import_contract_and_rejects_empty_inputs(self):
        script = ROOT / "kubernetes/apps/n8n/render-credentials.cjs"
        code = "const {render}=require(process.argv[1]);const fs=require('node:fs');process.stdout.write(render(...JSON.parse(fs.readFileSync(0,'utf8'))));"
        inputs = ['webhook\r\n"\\\u0000\u00e9${DOMAIN}', 'perplexity\r\n"\\\u0000\u00e9{{literal}}']
        result = subprocess.run(["node", "-e", code, str(script)], input=json.dumps(inputs).encode(), capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [
            {"id": "hermesWebhookTok", "name": "Hermes webhook", "type": "httpHeaderAuth",
             "data": {"name": "X-Gitlab-Token", "value": inputs[0]}},
            {"id": "perplexityDecider", "name": "Perplexity Decider", "type": "perplexityApi",
             "data": {"apiKey": inputs[1]}},
        ])
        for inputs in (["", "key"], ["token", ""], [None, "key"], ["token", {}]):
            result = subprocess.run(["node", "-e", code, str(script)], input=json.dumps(inputs).encode(), capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(result.stdout)

    def test_bcrypt_cost_and_scheme_validation(self):
        template = (ROOT / "kubernetes/apps/adguard/AdGuardHome.yaml.tmpl").read_text()
        for scheme in ("a", "b", "y"):
            for cost in ("04", "12", "31"):
                adguard.render(template, "user", f"$2{scheme}${cost}$" + "A" * 53)
        for cost in ("00", "03", "32", "99"):
            with self.assertRaises(ValueError):
                adguard.render(template, "user", f"$2b${cost}$" + "A" * 53)


if __name__ == "__main__":
    unittest.main()
