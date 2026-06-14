import json
from pathlib import Path

import pytest

from apps.minecraft.scripts import resolve_allowlist
from apps.minecraft.scripts.resolve_allowlist import (
    dashed_uuid,
    normalize_bedrock_name,
    write_ops,
    write_whitelist,
)


def test_dashed_uuid_formats_mojang_uuid():
    assert (
        dashed_uuid("57e13c3957554354aefab60195ff6f27")
        == "57e13c39-5755-4354-aefa-b60195ff6f27"
    )


def test_dashed_uuid_rejects_non_hex_characters():
    with pytest.raises(ValueError):
        dashed_uuid("z" * 32)


def test_canonical_uuid_accepts_dashed_and_undashed_uuid():
    assert (
        resolve_allowlist.canonical_uuid("00000000-0000-0000-0000-000000000001")
        == "00000000-0000-0000-0000-000000000001"
    )
    assert (
        resolve_allowlist.canonical_uuid("00000000000000000000000000000001")
        == "00000000-0000-0000-0000-000000000001"
    )


def test_normalize_bedrock_name_adds_floodgate_prefix_once():
    assert normalize_bedrock_name("holybaechuwu", ".") == ".holybaechuwu"
    assert normalize_bedrock_name(".holybaechuwu", ".") == ".holybaechuwu"


def test_parse_bedrock_spec_allows_optional_uuid_override():
    assert resolve_allowlist.parse_bedrock_spec("holybaechuwu") == (
        "holybaechuwu",
        None,
    )
    assert resolve_allowlist.parse_bedrock_spec(
        "holybaechuwu=00000000000000000000000000000001"
    ) == ("holybaechuwu", "00000000000000000000000000000001")


def test_write_whitelist_is_stable_and_reports_change(tmp_path: Path):
    output = tmp_path / "whitelist.json"
    entries = [
        {"uuid": "57e13c39-5755-4354-aefa-b60195ff6f27", "name": "holybaechu"},
        {"uuid": "00000000-0000-0000-0000-000000000001", "name": ".holybaechuwu"},
    ]

    assert write_whitelist(output, entries) == "changed"
    assert write_whitelist(output, entries) == "unchanged"

    assert json.loads(output.read_text(encoding="utf-8")) == entries
    assert output.read_text(encoding="utf-8").endswith("\n")


def test_write_whitelist_replaces_target_atomically(tmp_path: Path, monkeypatch):
    output = tmp_path / "whitelist.json"
    entries = [{"uuid": "57e13c39-5755-4354-aefa-b60195ff6f27", "name": "holybaechu"}]
    replaced = []
    path_type = type(output)
    original_replace = path_type.replace

    def recording_replace(self, target):
        replaced.append((self, Path(target)))
        return original_replace(self, target)

    monkeypatch.setattr(path_type, "replace", recording_replace)

    assert write_whitelist(output, entries) == "changed"
    assert write_whitelist(output, entries) == "unchanged"

    assert len(replaced) == 1
    temp_path, target_path = replaced[0]
    assert temp_path.parent == output.parent
    assert target_path == output
    assert not temp_path.exists()
    assert list(tmp_path.iterdir()) == [output]
    assert json.loads(output.read_text(encoding="utf-8")) == entries


def test_write_ops_uses_paper_operator_format(tmp_path: Path):
    output = tmp_path / "ops.json"
    entries = [
        {"uuid": "57e13c39-5755-4354-aefa-b60195ff6f27", "name": "holybaechu"},
        {"uuid": "00000000-0000-0000-0009-01f46fc76cf7", "name": ".holybaechuwu"},
    ]

    assert write_ops(output, entries) == "changed"
    assert write_ops(output, entries) == "unchanged"

    assert json.loads(output.read_text(encoding="utf-8")) == [
        {
            "uuid": "57e13c39-5755-4354-aefa-b60195ff6f27",
            "name": "holybaechu",
            "level": 4,
            "bypassesPlayerLimit": False,
        },
        {
            "uuid": "00000000-0000-0000-0009-01f46fc76cf7",
            "name": ".holybaechuwu",
            "level": 4,
            "bypassesPlayerLimit": False,
        },
    ]


def test_resolve_java_player_wraps_fetch_failure_with_player_context(monkeypatch):
    def fail_fetch_json(url):
        raise RuntimeError("boom")

    monkeypatch.setattr(resolve_allowlist, "fetch_json", fail_fetch_json)

    with pytest.raises(RuntimeError) as excinfo:
        resolve_allowlist.resolve_java_player("holybaechu")

    message = str(excinfo.value)
    assert "Java player holybaechu" in message
    assert "boom" in message


def test_resolve_java_player_wraps_invalid_json_with_player_context(monkeypatch):
    def fail_fetch_json(url):
        raise json.JSONDecodeError("bad json", "not-json", 0)

    monkeypatch.setattr(resolve_allowlist, "fetch_json", fail_fetch_json)

    with pytest.raises(RuntimeError) as excinfo:
        resolve_allowlist.resolve_java_player("holybaechu")

    message = str(excinfo.value)
    assert "Java player holybaechu" in message
    assert "bad json" in message


def test_resolve_bedrock_player_wraps_malformed_payload_with_player_context(
    monkeypatch,
):
    def fetch_json(url):
        return {}

    monkeypatch.setattr(resolve_allowlist, "fetch_json", fetch_json)

    with pytest.raises(RuntimeError) as excinfo:
        resolve_allowlist.resolve_bedrock_player("holybaechuwu", ".")

    assert "Bedrock player holybaechuwu" in str(excinfo.value)


@pytest.mark.parametrize(
    "uuid",
    [
        "00000000-0000-0000-0000-000000000001",
        "00000000000000000000000000000001",
    ],
)
def test_resolve_bedrock_player_uses_override_without_fetch(monkeypatch, uuid):
    def fail_fetch_json(url):
        raise AssertionError(f"fetch_json should not be called: {url}")

    monkeypatch.setattr(resolve_allowlist, "fetch_json", fail_fetch_json)

    result = resolve_allowlist.resolve_bedrock_player(
        "holybaechuwu",
        ".",
        uuid,
    )

    assert result == {
        "uuid": "00000000-0000-0000-0000-000000000001",
        "name": ".holybaechuwu",
    }


def test_resolve_bedrock_player_wraps_malformed_override_with_player_context():
    with pytest.raises(RuntimeError) as excinfo:
        resolve_allowlist.resolve_bedrock_player("holybaechuwu", ".", "not-a-uuid")

    message = str(excinfo.value)
    assert "Bedrock player holybaechuwu" in message
    assert "not-a-uuid" in message


def test_resolve_java_player_quotes_profile_url(monkeypatch):
    urls = []

    def fetch_json(url):
        urls.append(url)
        return {"id": "57e13c3957554354aefab60195ff6f27", "name": "Holy Bae/Chu"}

    monkeypatch.setattr(resolve_allowlist, "fetch_json", fetch_json)

    result = resolve_allowlist.resolve_java_player("Holy Bae/Chu")

    assert urls == [
        "https://api.mojang.com/users/profiles/minecraft/Holy%20Bae%2FChu"
    ]
    assert result == {
        "uuid": "57e13c39-5755-4354-aefa-b60195ff6f27",
        "name": "Holy Bae/Chu",
    }


def test_resolve_bedrock_player_quotes_prefixed_name_and_prefix(monkeypatch):
    urls = []

    def fetch_json(url):
        urls.append(url)
        return {
            "id": "00000000000000000000000000000001",
            "name": "+bed rock/chu",
        }

    monkeypatch.setattr(resolve_allowlist, "fetch_json", fetch_json)

    result = resolve_allowlist.resolve_bedrock_player("bed rock/chu", "+")

    assert urls == [
        "https://api.geysermc.org/v2/utils/uuid/bedrock_or_java/"
        "%2Bbed%20rock%2Fchu?prefix=%2B"
    ]
    assert result == {
        "uuid": "00000000-0000-0000-0000-000000000001",
        "name": "+bed rock/chu",
    }


def test_main_accepts_bedrock_uuid_override_and_writes_access_files(
    tmp_path, monkeypatch
):
    output = tmp_path / "whitelist.json"
    ops_output = tmp_path / "ops.json"

    def fail_fetch_json(url):
        raise AssertionError(f"fetch_json should not be called: {url}")

    monkeypatch.setattr(resolve_allowlist, "fetch_json", fail_fetch_json)
    monkeypatch.setattr(
        resolve_allowlist.sys,
        "argv",
        [
            "resolve_allowlist.py",
            "--bedrock",
            "holybaechuwu=00000000000000000000000000000001",
            "--bedrock-op",
            "holybaechuwu=00000000000000000000000000000001",
            "--output",
            str(output),
            "--ops-output",
            str(ops_output),
        ],
    )

    assert resolve_allowlist.main() == 0
    assert json.loads(output.read_text(encoding="utf-8")) == [
        {
            "uuid": "00000000-0000-0000-0000-000000000001",
            "name": ".holybaechuwu",
        }
    ]
    assert json.loads(ops_output.read_text(encoding="utf-8")) == [
        {
            "uuid": "00000000-0000-0000-0000-000000000001",
            "name": ".holybaechuwu",
            "level": 4,
            "bypassesPlayerLimit": False,
        }
    ]
