"""Prepare private, memory-backed Git credentials before Hermes starts."""
import base64
import os
from pathlib import Path
import re
import stat
import sys

from cryptography.hazmat.primitives import serialization as s
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa


def convert(private, public):
    # Accept one unencrypted PKCS#8 object, not ignored PEM/DER suffixes.
    match = re.fullmatch(
        rb"\s*-----BEGIN PRIVATE KEY-----\s+([A-Za-z0-9+/=\s]+)"
        rb"-----END PRIVATE KEY-----\s*", private)
    if match is None:
        raise ValueError("private-container")
    der = base64.b64decode(re.sub(rb"\s", b"", match[1]), validate=True)
    key = s.load_der_private_key(der, password=None)
    if not isinstance(key, (ed25519.Ed25519PrivateKey, rsa.RSAPrivateKey,
                            ec.EllipticCurvePrivateKey)):
        raise ValueError("private-algorithm")
    canonical = key.private_bytes(s.Encoding.DER, s.PrivateFormat.PKCS8, s.NoEncryption())
    if der != canonical:
        raise ValueError("private-container")
    if len(public.strip().splitlines()) != 1:
        raise ValueError("public-container")
    expected = s.load_ssh_public_key(public).public_bytes(s.Encoding.OpenSSH, s.PublicFormat.OpenSSH)
    derived = key.public_key().public_bytes(s.Encoding.OpenSSH, s.PublicFormat.OpenSSH)
    if expected != derived:
        raise ValueError("public-identity")
    converted = key.private_bytes(s.Encoding.PEM, s.PrivateFormat.OpenSSH, s.NoEncryption())
    reparsed = s.load_ssh_private_key(converted, password=None)
    if (reparsed.private_bytes(s.Encoding.DER, s.PrivateFormat.PKCS8, s.NoEncryption()) != canonical
            or reparsed.public_key().public_bytes(s.Encoding.OpenSSH, s.PublicFormat.OpenSSH) != expected):
        raise ValueError("converted-identity")
    return converted, derived + b"\n"


def read_projected(path, owner):
    # Kubernetes Secret entries are symlinks into a root-owned revision directory.
    root = path.parent.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("source-path")
    fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner
                or stat.S_IMODE(info.st_mode) != 0o400 or info.st_size > 65536):
            raise ValueError("source-permissions")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            value = stream.read(65537)
    finally:
        os.close(fd)
    if not value or len(value) > 65536:
        raise ValueError("source-size")
    return value


def write_credentials(directory, files, owner):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("destination-permissions")
        if os.listdir(fd):
            # Never clear or overwrite an existing Git identity, including symlinks.
            raise ValueError("destination-not-empty")
        for name, value in files.items():
            output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o400, dir_fd=fd)
            with os.fdopen(output, "wb") as stream:
                os.fchown(stream.fileno(), owner, owner)
                os.fchmod(stream.fileno(), 0o400)
                stream.write(value)
    finally:
        os.close(fd)


def main():
    stage = "source"
    try:
        source = Path("/var/run/coder-source")
        token = read_projected(source / "github-token", 0)
        private = read_projected(source / "signing-key", 0)
        public = read_projected(source / "signing-public-key", 0)
        stage = "conversion"
        converted, derived = convert(private, public)
        stage = "destination"
        write_credentials(Path("/opt/coder-credentials"), {
            "github-token": token,
            "signing-key": converted,
            "signing-key.pub": derived,
            "allowed-signers": b'holybaechu@gmail.com namespaces="git" ' + derived,
        }, 10000)
    except Exception:
        # Parser/OS exception strings may contain credential bytes or paths.
        print(f"Git credential initialization failed: {stage}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
