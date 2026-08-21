#!/usr/bin/env python3
"""Materialize one self-contained homelab release from its component secret bundle."""

from __future__ import annotations

import argparse
import base64
import binascii
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


class PreparationError(RuntimeError):
    """The release package or component secret bundle is invalid."""


ACCOUNT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
BCRYPT_HASH = re.compile(
    r"\$2[aby]\$(?:0[4-9]|[12][0-9]|3[01])\$[./A-Za-z0-9]{53}\Z"
)
QBITTORRENT_HASH = re.compile(
    r"@ByteArray\(([A-Za-z0-9+/]{22}==):([A-Za-z0-9+/]{86}==)\)\Z"
)


def _object(value: Any, *, name: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise PreparationError(f"{name} must contain exactly {sorted(keys)}")
    return value


def _text(value: Any, *, name: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise PreparationError(f"{name} must be a nonempty string of at most {maximum} characters")
    if any(character in value for character in ("\0", "\r", "\n")):
        raise PreparationError(f"{name} must be a single line")
    return value


def _account_name(value: Any, *, name: str) -> str:
    text = _text(value, name=name, maximum=64)
    if ACCOUNT_NAME.fullmatch(text) is None:
        raise PreparationError(f"{name} has an unsupported account name")
    return text


def _read_bundle(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PreparationError(f"cannot inspect component secret bundle: {path}") from error
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise PreparationError("component secret bundle must be a regular non-symlink file")
    if os.name == "posix" and metadata.st_mode & 0o077:
        raise PreparationError("component secret bundle permissions are broader than 0600")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise PreparationError("component secret bundle is not valid UTF-8 JSON") from error
    return _object(
        payload,
        name="component secret bundle",
        keys={
            "component",
            "version",
            "cloudflare",
            "adguard",
            "qbittorrent",
            "copyparty_users",
        },
    )


def _read_topology(path: Path) -> dict[str, str]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PreparationError(f"cannot inspect host topology: {path}") from error
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise PreparationError("host topology must be a regular non-symlink file")
    if metadata.st_size > 1024 * 1024:
        raise PreparationError("host topology exceeds the size limit")
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
        all_group = _object(document, name="topology", keys={"all"})["all"]
        if not isinstance(all_group, dict):
            raise PreparationError("topology all group must be an object")
        children = all_group.get("children")
        if not isinstance(children, dict):
            raise PreparationError("topology children must be an object")
        pve_group = children.get("pve_hosts")
        debian_group = children.get("debian")
        if not isinstance(pve_group, dict) or not isinstance(debian_group, dict):
            raise PreparationError("topology lacks required host groups")
        pve_hosts = pve_group.get("hosts")
        debian_hosts = debian_group.get("hosts")
        if not isinstance(pve_hosts, dict) or not isinstance(debian_hosts, dict):
            raise PreparationError("topology host groups are invalid")
        pve = pve_hosts.get("pve")
        apps = debian_hosts.get("docker_apps")
        openclaw = debian_hosts.get("openclaw")
        if not all(isinstance(host, dict) for host in (pve, apps, openclaw)):
            raise PreparationError("topology lacks a required application route host")
        values = {
            "PVE_HOST": pve.get("ansible_host"),
            "APPS_HOST": apps.get("ansible_host"),
            "OPENCLAW_HOST": openclaw.get("ansible_host"),
            "ROUTER_HOST": apps.get("gateway"),
        }
        for name, value in values.items():
            if not isinstance(value, str) or ipaddress.ip_address(value).version != 4:
                raise PreparationError(f"topology {name} must be an IPv4 address")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise PreparationError("host topology is not valid UTF-8 JSON") from error
    return values


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _safe_directory(path: Path, *, mode: int = 0o700) -> None:
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
            raise PreparationError(f"release output directory is unsafe: {path}")
    else:
        path.mkdir(parents=True, mode=mode)
    if os.name == "posix":
        path.chmod(mode)


def _atomic_write(path: Path, content: str, *, mode: int = 0o600) -> None:
    _safe_directory(path.parent)
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise PreparationError(f"release output file is unsafe: {path}")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        if os.name == "posix":
            temporary.chmod(mode)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _template(root: Path, name: str) -> str:
    path = root / "config" / name
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise PreparationError(f"release template is unsafe: {path}")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PreparationError(f"cannot read release template: {path}") from error


def _replace_exact(template: str, marker: str, value: str, *, count: int = 1) -> str:
    if template.count(marker) != count:
        raise PreparationError(f"release template marker is missing or duplicated: {marker}")
    return template.replace(marker, value)


def _qbittorrent_hash(value: Any) -> str:
    password_hash = _text(value, name="qBittorrent password hash", maximum=256)
    match = QBITTORRENT_HASH.fullmatch(password_hash)
    if match is None:
        raise PreparationError("qBittorrent password hash has an invalid PBKDF2 shape")
    try:
        salt = base64.b64decode(match.group(1), validate=True)
        digest = base64.b64decode(match.group(2), validate=True)
    except (ValueError, binascii.Error) as error:
        raise PreparationError("qBittorrent password hash is not canonical base64") from error
    if len(salt) != 16 or len(digest) != 64:
        raise PreparationError("qBittorrent password hash has invalid PBKDF2 lengths")
    return password_hash


def prepare(secret_bundle: Path, release_root: Path, topology: Path) -> None:
    if release_root.is_symlink() or not release_root.is_dir():
        raise PreparationError("release root must be a real directory")
    release_root = release_root.resolve()
    for required in (release_root / "release.json", release_root / "compose.yml"):
        if not required.is_file() or required.is_symlink():
            raise PreparationError(f"release package is incomplete: {required}")

    bundle = _read_bundle(secret_bundle)
    topology_values = _read_topology(topology)
    if bundle["component"] != "apps":
        raise PreparationError("component secret bundle component must be apps")
    if type(bundle["version"]) is not int or bundle["version"] != 1:
        raise PreparationError("component secret bundle version must be 1")

    cloudflare = _object(
        bundle["cloudflare"],
        name="cloudflare",
        keys={"traefik_dns_api_token", "ddns_api_token"},
    )
    traefik_token = _text(cloudflare["traefik_dns_api_token"], name="traefik DNS API token")
    ddns_token = _text(cloudflare["ddns_api_token"], name="DDNS API token")

    adguard = _object(
        bundle["adguard"],
        name="adguard",
        keys={"username", "password_hash"},
    )
    adguard_username = _account_name(adguard["username"], name="AdGuard username")
    adguard_hash = _text(adguard["password_hash"], name="AdGuard password hash", maximum=128)
    if BCRYPT_HASH.fullmatch(adguard_hash) is None:
        raise PreparationError("AdGuard password hash must be bcrypt")

    qbittorrent = _object(
        bundle["qbittorrent"],
        name="qbittorrent",
        keys={"username", "password_hash"},
    )
    qbittorrent_username = _account_name(
        qbittorrent["username"], name="qBittorrent username"
    )
    qbittorrent_hash = _qbittorrent_hash(qbittorrent["password_hash"])

    users = bundle["copyparty_users"]
    if not isinstance(users, list) or not users:
        raise PreparationError("copyparty_users must be a nonempty list")
    rendered_users: list[tuple[str, str]] = []
    for index, item in enumerate(users):
        account = _object(
            item,
            name=f"copyparty_users[{index}]",
            keys={"name", "password"},
        )
        rendered_users.append(
            (
                _account_name(account["name"], name=f"copyparty_users[{index}].name"),
                _text(
                    account["password"],
                    name=f"copyparty_users[{index}].password",
                    maximum=1024,
                ),
            )
        )
    names = [name for name, _password in rendered_users]
    if len(names) != len(set(names)):
        raise PreparationError("Copyparty account names must be unique")

    adguard_config = _template(release_root, "AdGuardHome.yaml.tmpl")
    adguard_config = _replace_exact(
        adguard_config, "@@ADGUARD_ADMIN_USERNAME@@", json.dumps(adguard_username)
    )
    adguard_config = _replace_exact(
        adguard_config, "@@ADGUARD_ADMIN_PASSWORD_HASH@@", json.dumps(adguard_hash)
    )
    adguard_config = _replace_exact(
        adguard_config, "@@APPS_HOST@@", topology_values["APPS_HOST"]
    )

    routes_config = _template(release_root, "routes.yml.tmpl")
    for name in ("PVE_HOST", "OPENCLAW_HOST", "ROUTER_HOST"):
        routes_config = _replace_exact(
            routes_config, f"@@{name}@@", topology_values[name]
        )

    copyparty_config = _template(release_root, "copyparty.conf.tmpl")
    account_lines = "\n".join(f"  {name}: {password}" for name, password in rendered_users)
    copyparty_config = _replace_exact(
        copyparty_config, "@@COPY_PARTY_ACCOUNTS@@", account_lines
    )
    copyparty_config = _replace_exact(
        copyparty_config, "@@COPY_PARTY_USER_NAMES@@", ", ".join(names)
    )
    copyparty_config = _replace_exact(
        copyparty_config, "@@COPY_PARTY_OWNER@@", names[0], count=4
    )

    qbittorrent_config = _template(release_root, "qBittorrent.conf.tmpl")
    qbittorrent_config = _replace_exact(
        qbittorrent_config, "@@QBITTORRENT_USERNAME@@", qbittorrent_username
    )
    qbittorrent_config = _replace_exact(
        qbittorrent_config,
        "@@QBITTORRENT_PASSWORD_HASH@@",
        qbittorrent_hash,
    )

    _atomic_write(release_root / ".secrets" / "traefik.env", f"CF_DNS_API_TOKEN={traefik_token}\n")
    _atomic_write(
        release_root / ".secrets" / "cloudflare-ddns.env",
        f"CLOUDFLARE_API_TOKEN={ddns_token}\n",
    )
    _atomic_write(
        release_root / "generated" / "adguard" / "AdGuardHome.yaml", adguard_config
    )
    _atomic_write(
        release_root / "generated" / "traefik" / "routes.yml", routes_config
    )
    _atomic_write(release_root / "generated" / "copyparty.conf", copyparty_config)
    _atomic_write(
        release_root / "generated" / "qbittorrent" / "qBittorrent.conf",
        qbittorrent_config,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--secret-bundle", required=True, type=Path)
    parser.add_argument(
        "--release-root", type=Path, default=Path(__file__).resolve().parent
    )
    parser.add_argument("--topology", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        prepare(args.secret_bundle, args.release_root, args.topology)
    except PreparationError as error:
        print(f"homelab release preparation failed: {error}", file=sys.stderr)
        return 1
    print("homelab release preparation completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
