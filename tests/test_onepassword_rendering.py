import base64
import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


staging = load("staging_render", "scripts/verify-onepassword-staging.py")
immich = load("immich_render", "kubernetes/apps/immich/render.py")
adguard = load("adguard_render", "kubernetes/apps/adguard/render.py")
qbittorrent = load("qbittorrent_render", "kubernetes/apps/qbittorrent/render.py")


def secret(data):
    return {"data": {key: base64.b64encode(value).decode() for key, value in data.items()}}


class RenderVerificationTests(unittest.TestCase):
    def test_immich_escaping_full_config_and_database_parity(self):
        template = (ROOT / "kubernetes/apps/immich/immich.json").read_text()
        for credential in ('quotes" backslash\\\n\r\n\t\u0000\u00e9', '${DOMAIN}{{literal}}'):
            with self.subTest(credential=credential):
                output = immich.render(template, credential)
                self.assertEqual(json.loads(output)["oauth"]["clientSecret"], credential)
                replacement = secret({"database-password": b"db\r\n", "oidc-client-secret": credential.encode()})
                original = secret({"DB_PASSWORD": b"db\r\n", "immich.json": output})
                configmap = {"data": {"immich.json": template}}
                staging.verify_immich_config(replacement, original, configmap)
                for mutate in (
                    lambda s: s["data"].update(DB_PASSWORD=base64.b64encode(b"db\n").decode()),
                    lambda s: s["data"].update(extra=""),
                    lambda s: s["data"].update({"immich.json": base64.b64encode(b'{}').decode()}),
                ):
                    changed = copy.deepcopy(original)
                    mutate(changed)
                    with self.assertRaises(ValueError):
                        staging.verify_immich_config(replacement, changed, configmap)
                changed_config = json.loads(output)
                changed_config["server"]["publicUsers"] = True
                changed = secret({"DB_PASSWORD": b"db\r\n", "immich.json": json.dumps(changed_config).encode()})
                with self.assertRaisesRegex(ValueError, "immich-config-content"):
                    staging.verify_immich_config(replacement, changed, configmap)

    def test_immich_refuses_empty_or_embedded_credentials(self):
        for template, credential in (('{"oauth":{}}', ''), ('{"oauth":{"clientSecret":"x"}}', 'x'),
                                     ('{"oauth":null}', 'x')):
            with self.assertRaises(ValueError):
                immich.render(template, credential)

    def test_validated_hash_files_match_checker_exactly(self):
        cases = (
            (adguard, "adguard", "AdGuardHome.yaml", "$2b$12$" + "A" * 53),
            (qbittorrent, "qbittorrent", "managed.conf", "@ByteArray(" + "A" * 22 + "==:" + "B" * 86 + "==)"),
        )
        for module, app, key, password_hash in cases:
            with self.subTest(app=app):
                template = (ROOT / f"kubernetes/apps/{app}/{key}.tmpl").read_text()
                output = module.render(template, "user-name.1", password_hash)
                replacement = secret({"username": b"user-name.1", "password-hash": password_hash.encode()})
                original = secret({key: output})
                configmap = {"data": {key: template}}
                contract = staging.CONTRACTS[app + "-connect"]
                staging.verify_rendered_config(replacement, original, configmap, contract)
                original["data"][key] = base64.b64encode(output + b"\n").decode()
                with self.assertRaisesRegex(ValueError, "eso-rendered-config-bytes"):
                    staging.verify_rendered_config(replacement, original, configmap, contract)
                for username in ("", "a\nadmin", "a\r", "a" * 65, "{{ .value }}", "a=1"):
                    with self.assertRaises(ValueError):
                        module.render(template, username, password_hash)
                for invalid_hash in ("", password_hash + "\n", "\"\nadmin", password_hash[:-1]):
                    with self.assertRaises(ValueError):
                        module.render(template, "user", invalid_hash)
                with self.assertRaisesRegex(ValueError, "unsupported template"):
                    module.render(template + "{{ .unreviewed }}", "user", password_hash)

    def test_bcrypt_cost_and_scheme_validation(self):
        template = (ROOT / "kubernetes/apps/adguard/AdGuardHome.yaml.tmpl").read_text()
        for scheme in ("a", "b", "y"):
            for cost in ("04", "12", "31"):
                adguard.render(template, "user", f"$2{scheme}${cost}$" + "A" * 53)
        for cost in ("00", "03", "32", "99"):
            with self.assertRaises(ValueError):
                adguard.render(template, "user", f"$2b${cost}$" + "A" * 53)

    def test_authentik_all_eight_keys_require_exact_bytes(self):
        replacements = {
            "authentik-connect": secret({"secret-key": b"key", "database-password": b"db",
                                         "email": b"email", "password": b"password"}),
            "headscale-connect": secret({"oidc-client-secret": b"headscale"}),
            "zipline-connect": secret({"oidc-client-secret": b"zipline"}),
            "immich-connect": secret({"oidc-client-secret": b"immich"}),
            "langfuse-connect": secret({"oidc-client-secret": b"langfuse"}),
        }
        original = secret(dict(zip((
            "AUTHENTIK_SECRET_KEY", "AUTHENTIK_POSTGRESQL__PASSWORD", "AUTHENTIK_BOOTSTRAP_EMAIL",
            "AUTHENTIK_BOOTSTRAP_PASSWORD", "HEADSCALE_OIDC_CLIENT_SECRET", "ZIPLINE_OIDC_CLIENT_SECRET",
            "IMMICH_OIDC_CLIENT_SECRET", "LANGFUSE_OIDC_CLIENT_SECRET",
        ), (b"key", b"db", b"email", b"password", b"headscale", b"zipline", b"immich", b"langfuse"))))
        staging.verify_authentik_env(original, replacements)
        for key in original["data"]:
            changed = copy.deepcopy(original)
            changed["data"][key] = base64.b64encode(b"changed").decode()
            with self.assertRaisesRegex(ValueError, "eso-replacement-bytes"):
                staging.verify_authentik_env(changed, replacements)
        original["data"]["extra"] = ""
        with self.assertRaisesRegex(ValueError, "eso-keys"):
            staging.verify_authentik_env(original, replacements)


if __name__ == "__main__":
    unittest.main()
