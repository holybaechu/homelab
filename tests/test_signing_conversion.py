import base64
import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization as s
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa


spec = importlib.util.spec_from_file_location(
    "signing_staging", Path(__file__).parents[1] / "scripts/verify-onepassword-staging.py"
)
assert spec is not None and spec.loader is not None
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)


def representations(key):
    return (key.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()),
            key.private_bytes(s.Encoding.PEM, s.PrivateFormat.OpenSSH, s.NoEncryption()),
            key.public_key().public_bytes(s.Encoding.OpenSSH, s.PublicFormat.OpenSSH))


class SigningConversionTests(unittest.TestCase):
    def test_real_library_conversion_preserves_private_public_identity(self):
        for key in (ed25519.Ed25519PrivateKey.generate(),
                    rsa.generate_private_key(public_exponent=65537, key_size=2048),
                    ec.generate_private_key(ec.SECP256R1())):
            with self.subTest(algorithm=type(key).__name__):
                raw, reference, public = representations(key)
                converted = staging.verify_signing_identity(raw, reference, public + b" comment")
                self.assertEqual(staging.private_key_format(converted), "openssh")
                # Exercise an independent OpenSSH parser when available, never echo keys.
                if shutil.which("ssh-keygen"):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "key"
                        path.write_bytes(converted)
                        path.chmod(0o600)
                        result = subprocess.run(["ssh-keygen", "-y", "-f", str(path)],
                                                capture_output=True, check=True)
                        self.assertEqual(result.stdout.strip(), public)

    def test_unrelated_private_or_public_identity_is_rejected(self):
        raw, reference, public = representations(ed25519.Ed25519PrivateKey.generate())
        _, other_reference, other_public = representations(ed25519.Ed25519PrivateKey.generate())
        with self.assertRaisesRegex(ValueError, "signing-private-identity"):
            staging.verify_signing_identity(raw, other_reference, public)
        with self.assertRaisesRegex(ValueError, "signing-public-identity"):
            staging.verify_signing_identity(raw, reference, other_public)

    def test_invalid_or_wrong_containers_are_rejected(self):
        raw, reference, public = representations(ed25519.Ed25519PrivateKey.generate())
        for args in ((reference, reference, public), (raw, raw, public),
                     (b"-----BEGIN PRIVATE KEY-----\ninvalid", reference, public),
                     (raw, reference, b"invalid")):
            with self.subTest(case=args[0].splitlines()[0]):
                with self.assertRaises(ValueError):
                    staging.verify_signing_identity(*args)

    def test_administrator_failure_never_exposes_exception_or_resources(self):
        output = io.StringIO()
        with patch.object(staging, "read_resource", side_effect=ValueError("PRIVATE_VALUE")):
            with contextlib.redirect_stderr(output):
                self.assertEqual(staging.main(["--signing-conversion"]), 1)
        self.assertEqual(output.getvalue(),
                         "FAIL signing-conversion: stage=read-github-connect category=validation-failed no-writes\n")

    def test_retained_contract_distinguishes_key_set_reference_and_github(self):
        raw, reference, public = representations(ed25519.Ed25519PrivateKey.generate())
        replacements = {"github-connect": {"credential": b"test-token"},
                        "signing-connect": {"private-key": raw, "public-key": public}}
        original = {"github-token": b"test-token", "signing-key": reference}
        staging.verify_retained_signing(original, replacements, reference)
        cases = [({"signing-key": reference}, reference, "retained-key-set"),
                 ({**original, "PRIVATE_VALUE": b"unknown"}, reference, "retained-key-set"),
                 (original, b"", "retained-reference-empty"),
                 (original, b"different", "signing-container-format"),
                 ({**original, "github-token": b"different"}, reference, "retained-github-credential-bytes")]
        for retained, reference, category in cases:
            with self.subTest(category=category):
                with self.assertRaisesRegex(staging.CheckFailure, "^" + category + "$"):
                    staging.verify_retained_signing(retained, replacements, reference)

    def test_same_identity_whitespace_does_not_waive_unknown_keys(self):
        raw, reference, public = representations(ed25519.Ed25519PrivateKey.generate())
        replacements = {"github-connect": {"credential": b"test-token"},
                        "signing-connect": {"private-key": raw, "public-key": public}}
        retained = {"github-token": b"test-token", "signing-key": reference + b"\n",
                    "PRIVATE_VALUE": b"unreviewed"}
        resources = {name: {"data": {k: base64.b64encode(v).decode() for k, v in data.items()}}
                     for name, data in {**replacements, "hermes-coder-git": retained}.items()}
        output, errors = io.StringIO(), io.StringIO()
        with (patch.object(staging, "read_resource", side_effect=lambda kind, name, ns: resources[name]),
              patch.object(staging, "read_source", return_value={}),
              patch.object(staging, "verify"), patch.object(staging, "verify_source"),
              patch.object(staging.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, reference)) as command,
              contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors)):
            self.assertEqual(staging.main(["--signing-conversion"]), 1)
        self.assertIn("category=retained-key-set", errors.getvalue())
        self.assertIn("unreviewed-count=1", output.getvalue())
        self.assertIn("retained-reference-bytes-match=False", output.getvalue())
        self.assertIn("reference-identity=private-public-match", output.getvalue())
        self.assertIn("retained-identity=private-public-match", output.getvalue())
        self.assertNotIn("PRIVATE_VALUE", output.getvalue() + errors.getvalue())
        self.assertNotIn(reference.decode(), output.getvalue() + errors.getvalue())
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args.args[0][:3], ["op", "read", "--no-newline"])
        retained.pop("PRIVATE_VALUE")
        staging.verify_retained_signing(retained, replacements, reference)

    def test_randomized_containers_require_same_source_identity_for_all_algorithms(self):
        for key in (ed25519.Ed25519PrivateKey.generate(),
                    rsa.generate_private_key(public_exponent=65537, key_size=2048),
                    ec.generate_private_key(ec.SECP256R1())):
            with self.subTest(algorithm=type(key).__name__):
                raw, reference, public = representations(key)
                _, retained, _ = representations(key)
                self.assertNotEqual(reference, retained)
                replacements = {"github-connect": {"credential": b"test-token"},
                                "signing-connect": {"private-key": raw, "public-key": public}}
                original = {"github-token": b"test-token", "signing-key": retained}
                staging.verify_retained_signing(original, replacements, reference)
                _, _, other_public = representations(ed25519.Ed25519PrivateKey.generate())
                with self.assertRaisesRegex(staging.CheckFailure, "signing-public-identity"):
                    staging.verify_retained_signing(original,
                        {**replacements, "signing-connect": {"private-key": raw, "public-key": other_public}},
                        reference)
                _, other, _ = representations(ed25519.Ed25519PrivateKey.generate())
                for old, ref in ((other, reference), (retained, other)):
                    with self.assertRaisesRegex(staging.CheckFailure, "signing-private-identity"):
                        staging.verify_retained_signing({**original, "signing-key": old}, replacements, ref)
                with self.assertRaisesRegex(staging.CheckFailure, "retained-github-credential-bytes"):
                    staging.verify_retained_signing({**original, "github-token": b"different"}, replacements, reference)

    def test_valid_comment_drift_and_invalid_wrappers_are_not_randomness(self):
        raw, reference, public = representations(ed25519.Ed25519PrivateKey.generate())
        replacements = {"github-connect": {"credential": b"test-token"},
                        "signing-connect": {"private-key": raw, "public-key": public}}
        # OpenSSH itself rewrites only the comment (and serialization randomness).
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            path.write_bytes(reference)
            path.chmod(0o600)
            subprocess.run(["ssh-keygen", "-c", "-C", "changed-comment", "-P", "", "-f", str(path)],
                           capture_output=True, check=True)
            changed = path.read_bytes()
        staging.verify_signing_identity(raw, changed, public)
        for candidate in (changed, reference + b"unreviewed-trailer", b"unreviewed-prefix" + reference):
            with self.assertRaises(staging.CheckFailure):
                staging.verify_retained_signing(
                    {"github-token": b"test-token", "signing-key": candidate}, replacements, reference)
        corrupted = bytearray(base64.b64decode(b"".join(reference.splitlines()[1:-1])))
        corrupted[-1] ^= 1
        invalid = (b"-----BEGIN OPENSSH PRIVATE KEY-----\n" + base64.b64encode(corrupted)
                   + b"\n-----END OPENSSH PRIVATE KEY-----\n")
        with self.assertRaisesRegex(staging.CheckFailure, "signing-reference-private-invalid"):
            staging.verify_retained_signing(
                {"github-token": b"test-token", "signing-key": invalid}, replacements, reference)

    def test_identity_diagnostics_distinguish_private_public_and_parser_failures(self):
        raw, reference, public = representations(ed25519.Ed25519PrivateKey.generate())
        _, other_reference, other_public = representations(ed25519.Ed25519PrivateKey.generate())
        cases = [(other_reference, public, "signing-private-identity"),
                 (reference, other_public, "signing-public-identity"),
                 (b"-----BEGIN OPENSSH PRIVATE KEY-----\nPRIVATE_VALUE", public,
                  "signing-reference-private-invalid"),
                 (reference, b"PRIVATE_VALUE", "signing-public-invalid")]
        for candidate, supplied_public, category in cases:
            replacements = {"github-connect": {"credential": b"test-token"},
                            "signing-connect": {"private-key": raw, "public-key": supplied_public}}
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                staging.signing_retained_diagnostics(
                    {"github-token": b"test-token", "signing-key": candidate}, replacements, reference)
            self.assertIn("retained-identity=" + category, output.getvalue())
            self.assertNotIn("PRIVATE_VALUE", output.getvalue())


if __name__ == "__main__":
    unittest.main()
