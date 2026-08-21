#!/usr/bin/env python3
"""Bind the configured deploy private key to the PVE access bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Sequence


PUBLIC_KEY_RE = re.compile(
    r"(?:ssh-ed25519|ecdsa-sha2-nistp256|rsa-sha2-512) "
    r"(?:[A-Za-z0-9+/]{4})*"
    r"(?:[A-Za-z0-9+/]{4}|[A-Za-z0-9+/]{3}=|[A-Za-z0-9+/]{2}==)\Z"
)


class AccessContractError(RuntimeError):
    """The controller identity and PVE bundle do not form one access contract."""


def _regular_file(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AccessContractError(f"{label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise AccessContractError(f"{label} must be a regular non-symlink file")
    return path


def derive_public_key(private_key: Path) -> str:
    """Derive one normalized public identity without exposing private material."""

    private_key = _regular_file(Path(private_key), "deploy private key")
    try:
        result = subprocess.run(
            ["ssh-keygen", "-y", "-f", str(private_key)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as exc:
        raise AccessContractError("cannot execute ssh-keygen") from exc
    if result.returncode != 0:
        raise AccessContractError("cannot derive the deploy public key")
    lines = result.stdout.splitlines()
    if len(lines) != 1:
        raise AccessContractError("derived deploy public key is not one normalized line")
    fields = lines[0].split()
    if len(fields) < 2:
        raise AccessContractError("derived deploy public key is incomplete")
    public_key = " ".join(fields[:2])
    if PUBLIC_KEY_RE.fullmatch(public_key) is None:
        raise AccessContractError("derived deploy public key is not normalized or allowed")
    return public_key


def _load_bundle(path: Path) -> dict[str, Any]:
    path = _regular_file(Path(path), "PVE component bundle")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AccessContractError("PVE component bundle is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise AccessContractError("PVE component bundle must be an object")
    return value


def require_identity_membership(bundle_path: Path, public_key: str) -> None:
    """Require exact membership in the same normalized key contract Ansible uses."""

    if PUBLIC_KEY_RE.fullmatch(public_key) is None:
        raise AccessContractError("deploy public key is not normalized or allowed")
    bundle = _load_bundle(bundle_path)
    if set(bundle) != {"component", "values", "version"}:
        raise AccessContractError("PVE component bundle has an invalid field set")
    if bundle.get("component") != "pve" or type(bundle.get("version")) is not int:
        raise AccessContractError("PVE component bundle identity is invalid")
    if bundle["version"] != 1 or not isinstance(bundle.get("values"), dict):
        raise AccessContractError("PVE component bundle contract is invalid")
    values = bundle["values"]
    if set(values) != {"deploy_ssh_public_keys"}:
        raise AccessContractError("PVE component bundle values have an invalid field set")
    keys = values["deploy_ssh_public_keys"]
    if (
        not isinstance(keys, list)
        or not keys
        or any(not isinstance(key, str) or PUBLIC_KEY_RE.fullmatch(key) is None for key in keys)
    ):
        raise AccessContractError("PVE deploy public keys are not normalized")
    if public_key not in keys:
        raise AccessContractError(
            "configured deploy identity is absent from PVE deploy_ssh_public_keys"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        public_key = derive_public_key(args.private_key)
        require_identity_membership(args.bundle, public_key)
    except AccessContractError as exc:
        print(f"pve-access-contract: {exc}", file=sys.stderr)
        return 2
    print("PVE deploy identity matches one authorized bundle key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
