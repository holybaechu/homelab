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
                         "FAIL signing-conversion: stage=read-github-connect no-writes\n")


if __name__ == "__main__":
    unittest.main()
