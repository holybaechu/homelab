import contextlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization as s
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

spec = importlib.util.spec_from_file_location(
    "hermes_git", Path(__file__).parents[1] / "kubernetes/apps/hermes/coder/configure_git.py")
assert spec is not None and spec.loader is not None
git = importlib.util.module_from_spec(spec)
spec.loader.exec_module(git)


def source(key):
    return (key.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()),
            key.public_key().public_bytes(s.Encoding.OpenSSH, s.PublicFormat.OpenSSH))


class HermesGitTests(unittest.TestCase):
    def test_real_algorithms_sign_and_verify_with_git_and_openssh(self):
        for key in (ed25519.Ed25519PrivateKey.generate(),
                    rsa.generate_private_key(public_exponent=65537, key_size=2048),
                    ec.generate_private_key(ec.SECP256R1())):
            with self.subTest(algorithm=type(key).__name__), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                private, public = source(key)
                converted, derived = git.convert(private, public + b" reviewed comment")
                credentials = directory / "credentials"
                credentials.mkdir(mode=0o700)
                git.write_credentials(credentials, {
                    "signing-key": converted, "signing-key.pub": derived,
                    "allowed-signers": b'holybaechu@gmail.com namespaces="git" ' + derived,
                    "github-token": b"synthetic-token\n",
                }, os.getuid())
                for path in credentials.iterdir():
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)
                    self.assertEqual(path.stat().st_uid, os.getuid())
                self.assertEqual((credentials / "github-token").read_bytes(), b"synthetic-token\n")
                parsed = subprocess.run(["ssh-keygen", "-y", "-P", "", "-f",
                                         str(credentials / "signing-key")], check=True, capture_output=True)
                self.assertEqual(parsed.stdout, derived)
                repo = directory / "repo"
                repo.mkdir()
                env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
                def run(*args):
                    return subprocess.run(["git", "-C", str(repo), *args], env=env,
                                          check=True, capture_output=True)
                run("init", "-q")
                for name, value in {
                    "user.name": "holybaechu", "user.email": "holybaechu@gmail.com",
                    "gpg.format": "ssh", "user.signingkey": str(credentials / "signing-key"),
                    "gpg.ssh.allowedSignersFile": str(credentials / "allowed-signers"),
                    "commit.gpgsign": "true",
                }.items():
                    run("config", name, value)
                run("commit", "--allow-empty", "-m", "synthetic signing test")
                run("verify-commit", "HEAD")

    def test_wrong_public_identity_and_malformed_public_are_rejected(self):
        private, public = source(ed25519.Ed25519PrivateKey.generate())
        _, other = source(ed25519.Ed25519PrivateKey.generate())
        for candidate in (other, b"not-a-key", public + b"\n" + other):
            with self.assertRaises(ValueError):
                git.convert(private, candidate)

    def test_strict_private_container_and_encryption(self):
        key = ed25519.Ed25519PrivateKey.generate()
        private, public = source(key)
        encrypted = key.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8,
                                      s.BestAvailableEncryption(b"synthetic-password"))
        for candidate in (private + private, private + b"secret-suffix", b"bad", encrypted,
                          key.private_bytes(s.Encoding.PEM, s.PrivateFormat.OpenSSH, s.NoEncryption())):
            with self.assertRaises(ValueError):
                git.convert(candidate, public)
        der = key.private_bytes(s.Encoding.DER, s.PrivateFormat.PKCS8, s.NoEncryption())
        import base64
        trailing_der = (b"-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(der + b"extra")
                        + b"\n-----END PRIVATE KEY-----\n")
        with self.assertRaises(ValueError):
            git.convert(trailing_der, public)
        git.convert(b" \n" + private.replace(b"\n", b"\r\n") + b"\t", public)

    def test_projected_source_modes_owner_and_symlink_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mount = root / "mount"
            revision = mount / "..revision"
            revision.mkdir(parents=True)
            path = revision / "key"
            path.write_bytes(b"synthetic-source")
            path.chmod(0o400)
            (mount / "..data").symlink_to("..revision")
            (mount / "key").symlink_to("..data/key")
            self.assertEqual(git.read_projected(mount / "key", os.getuid()), b"synthetic-source")
            with self.assertRaises(ValueError):
                git.read_projected(mount / "key", os.getuid() + 1)
            for mode in (0o444, 0o600, 0o777):
                path.chmod(mode)
                with self.assertRaises(ValueError):
                    git.read_projected(mount / "key", os.getuid())
            outside = root / "outside"
            outside.write_bytes(b"outside")
            outside.chmod(0o400)
            (mount / "escape").symlink_to(outside)
            with self.assertRaises(ValueError):
                git.read_projected(mount / "escape", os.getuid())
            with self.assertRaises(ValueError):
                git.read_projected(revision, os.getuid())
            os.mkfifo(mount / "fifo", 0o400)
            with self.assertRaises(ValueError):
                git.read_projected(mount / "fifo", os.getuid())

    def test_destination_never_overwrites_or_clears_existing_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            existing = directory / "signing-key"
            existing.write_bytes(b"existing-identity")
            with self.assertRaises(ValueError):
                git.write_credentials(directory, {"signing-key": b"replacement"}, os.getuid())
            self.assertEqual(existing.read_bytes(), b"existing-identity")
            existing.unlink()
            existing.symlink_to(directory / "missing")
            with self.assertRaises(ValueError):
                git.write_credentials(directory, {"signing-key": b"replacement"}, os.getuid())
            self.assertTrue(existing.is_symlink())

    def test_destination_permissions_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "credentials"
            directory.mkdir(mode=0o755)
            with self.assertRaises(ValueError):
                git.write_credentials(directory, {"key": b"value"}, os.getuid())
            directory.chmod(0o700)
            with self.assertRaises(ValueError):
                git.write_credentials(directory, {"key": b"value"}, os.getuid() + 1)
            link = Path(tmp) / "link"
            link.symlink_to(directory, target_is_directory=True)
            with self.assertRaises(OSError):
                git.write_credentials(link, {"key": b"value"}, os.getuid())
            self.assertEqual(list(directory.iterdir()), [])

    def test_failures_emit_only_fixed_stage_without_credentials(self):
        for call, stage in (("read_projected", "source"), ("convert", "conversion"),
                            ("write_credentials", "destination")):
            with self.subTest(stage=stage), contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(git, "read_projected", return_value=b"synthetic"))
                stack.enter_context(patch.object(git, "convert", return_value=(b"private", b"public")))
                stack.enter_context(patch.object(git, call, side_effect=ValueError("DO_NOT_PRINT")))
                output = io.StringIO()
                with contextlib.redirect_stderr(output):
                    self.assertEqual(git.main(), 1)
                self.assertEqual(output.getvalue(), f"Git credential initialization failed: {stage}\n")


if __name__ == "__main__":
    unittest.main()
