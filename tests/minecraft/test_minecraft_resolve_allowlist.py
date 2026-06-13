import json
from pathlib import Path

import pytest

from apps.minecraft.scripts import resolve_allowlist
from apps.minecraft.scripts.resolve_allowlist import (
    dashed_uuid,
    normalize_bedrock_name,
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


def test_normalize_bedrock_name_adds_floodgate_prefix_once():
    assert normalize_bedrock_name("holybaechuwu", ".") == ".holybaechuwu"
    assert normalize_bedrock_name(".holybaechuwu", ".") == ".holybaechuwu"


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
