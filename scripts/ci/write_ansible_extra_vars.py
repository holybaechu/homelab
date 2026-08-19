#!/usr/bin/env python3
"""Write component-scoped Ansible extra vars with private atomic replacement."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "infra" / "deployment" / "secrets.json"
SCHEMA_FIELDS = {
    "github_env",
    "ansible_variable",
    "component",
    "kind",
    "validation",
}
VALIDATION_TYPES = {
    "nonempty",
    "copyparty_users_json",
    "hex_64",
    "non_whitespace_1_4096",
    "non_whitespace_20_4096",
}


def load_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot load deployment secret schema {path}: {exc}") from exc

    if not isinstance(schema, dict) or schema.get("version") != 1:
        raise SystemExit("deployment secret schema must be a version 1 object")
    components = schema.get("components")
    entries = schema.get("entries")
    if (
        not isinstance(components, list)
        or not components
        or any(not isinstance(component, str) or not component for component in components)
        or len(components) != len(set(components))
    ):
        raise SystemExit("deployment secret schema components must be unique names")
    if not isinstance(entries, list) or not entries:
        raise SystemExit("deployment secret schema entries must be a non-empty list")

    github_names: set[str] = set()
    ansible_names: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or set(entry) != SCHEMA_FIELDS:
            raise SystemExit(f"deployment secret schema entry #{index} has invalid fields")
        if any(not isinstance(entry[field], str) or not entry[field] for field in SCHEMA_FIELDS):
            raise SystemExit(f"deployment secret schema entry #{index} has an empty field")
        if entry["component"] not in components:
            raise SystemExit(
                f"deployment secret schema entry #{index} has an unknown component"
            )
        if entry["kind"] not in {"required", "optional"}:
            raise SystemExit(f"deployment secret schema entry #{index} has an invalid kind")
        if entry["validation"] not in VALIDATION_TYPES:
            raise SystemExit(
                f"deployment secret schema entry #{index} has an unknown validation type"
            )
        if entry["github_env"] in github_names:
            raise SystemExit("deployment secret schema repeats a GitHub environment name")
        if entry["ansible_variable"] in ansible_names:
            raise SystemExit("deployment secret schema repeats an Ansible variable")
        github_names.add(entry["github_env"])
        ansible_names.add(entry["ansible_variable"])
    return schema


SECRET_SCHEMA = load_schema()
AVAILABLE_COMPONENTS = tuple(SECRET_SCHEMA["components"])


def parse_components(value: str) -> frozenset[str]:
    if not isinstance(value, str) or not value:
        raise SystemExit("deployment components must be a non-empty comma-separated set")
    pieces = value.split(",")
    if any(not piece.strip() for piece in pieces):
        raise SystemExit("deployment components must not contain empty names")
    selected = frozenset(piece.strip() for piece in pieces)
    unknown = sorted(selected.difference(AVAILABLE_COMPONENTS))
    if unknown:
        expected = ",".join(AVAILABLE_COMPONENTS)
        raise SystemExit(
            f"unknown deployment component(s): {','.join(unknown)}; expected a subset of {expected}"
        )
    return selected


def validate_copyparty_users(environment_name: str, value: str) -> list[dict[str, Any]]:
    try:
        users = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{environment_name} must be valid JSON: {exc}") from exc

    if not isinstance(users, list) or not users:
        raise SystemExit(f"{environment_name} must be a non-empty JSON list")
    for index, user in enumerate(users):
        if not isinstance(user, dict):
            raise SystemExit(f"copyparty user #{index + 1} must be an object")
        if not isinstance(user.get("name"), str) or not user["name"].strip():
            raise SystemExit(f"copyparty user #{index + 1} must include a non-empty name")
        if "password_hash" in user:
            raise SystemExit(
                f"{environment_name} must use plaintext password, not password_hash"
            )
        if not isinstance(user.get("password"), str) or not user["password"]:
            raise SystemExit(
                f"copyparty user {user.get('name', index + 1)!r} must include password"
            )
    return users


def validate_value(validation: str, environment_name: str, value: str) -> Any:
    if validation == "nonempty":
        return value
    if validation == "copyparty_users_json":
        return validate_copyparty_users(environment_name, value)

    stripped = value.strip()
    if validation == "hex_64":
        if re.fullmatch(r"[0-9a-fA-F]{64}", stripped) is None:
            raise SystemExit(
                f"{environment_name} must be exactly 64 hexadecimal characters"
            )
        return stripped

    bounds = {
        "non_whitespace_1_4096": (1, 4096),
        "non_whitespace_20_4096": (20, 4096),
    }
    minimum, maximum = bounds[validation]
    if not minimum <= len(stripped) <= maximum or any(
        character.isspace() for character in stripped
    ):
        raise SystemExit(
            f"{environment_name} must be {minimum}-{maximum} non-whitespace characters"
        )
    return stripped


def build_mapping_for_components(selected: frozenset[str]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for entry in SECRET_SCHEMA["entries"]:
        if entry["component"] not in selected:
            continue
        environment_name = entry["github_env"]
        value = os.environ.get(environment_name)
        if value is None or value == "":
            if entry["kind"] == "required":
                raise SystemExit(f"{environment_name} is required")
            continue
        mapping[entry["ansible_variable"]] = validate_value(
            entry["validation"], environment_name, value
        )
    return mapping


def build_mapping(components: str) -> dict[str, Any]:
    return build_mapping_for_components(parse_components(components))


def write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous_umask = os.umask(0o077)
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
    finally:
        os.umask(previous_umask)

    temporary_path = Path(temporary_name)
    descriptor_open = True
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:
            temporary_path.chmod(0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor_open = False
            json.dump(payload, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        if descriptor_open:
            os.close(descriptor)
        if temporary_path.exists():
            temporary_path.unlink()


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise SystemExit(
            "usage: write_ansible_extra_vars.py OUTPUT_JSON COMPONENT[,COMPONENT...]"
        )

    selected = parse_components(argv[2])
    payload = build_mapping_for_components(selected)
    promoted = os.environ.get("OPENCLAW_SETUP_COMMIT", "")
    if "openclaw" in selected and promoted:
        if re.fullmatch(r"[0-9a-f]{40}", promoted) is None:
            raise SystemExit("OPENCLAW_SETUP_COMMIT must be a lowercase 40-character SHA")
        payload["openclaw_setup_expected_commit"] = promoted
    write_private_json(Path(argv[1]), payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
