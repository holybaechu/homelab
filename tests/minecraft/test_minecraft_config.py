from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def load_yaml_text(content: str, source: str) -> dict:
    try:
        import yaml
    except ModuleNotFoundError:
        pytest.fail("PyYAML is required to parse Minecraft YAML config")

    class UniqueKeyLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                raise AssertionError(f"Duplicate YAML key {key!r} in {source}")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping,
    )

    data = yaml.load(content, Loader=UniqueKeyLoader)
    assert isinstance(data, dict), f"{source} must contain a YAML mapping"
    return data


def load_yaml(relative_path: str) -> dict:
    return load_yaml_text(read(relative_path), relative_path)


def iter_scalar_values(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from iter_scalar_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_scalar_values(child)
    else:
        yield value


def assert_no_latest_aliases(data: dict) -> None:
    for value in iter_scalar_values(data):
        if isinstance(value, str):
            assert "latest" not in value.lower()


def assert_digest_pin(data: dict, key: str, expected_length: int) -> None:
    digest = data[key]

    assert isinstance(digest, str)
    assert len(digest) == expected_length
    assert all(char in "0123456789abcdef" for char in digest)


def test_minecraft_yaml_loader_rejects_duplicate_keys():
    with pytest.raises(AssertionError, match="Duplicate YAML key 'minecraft_java_port'"):
        load_yaml_text(
            "minecraft_java_port: 25565\nminecraft_java_port: 25566\n",
            "inline minecraft vars",
        )


def test_minecraft_group_vars_pin_runtime_versions_and_ports():
    group_vars = load_yaml("infra/ansible/inventory/prod/group_vars/minecraft.yml")

    assert group_vars["minecraft_java_port"] == 25565
    assert group_vars["minecraft_bedrock_port"] == 19132
    assert group_vars["minecraft_paper_port"] == 25566
    assert group_vars["minecraft_java_package"] == "openjdk-25-jre-headless"
    assert group_vars["minecraft_paper_version"] == "26.1.2"
    assert group_vars["minecraft_paper_build"] == 70
    assert group_vars["minecraft_velocity_version"] == "3.5.0-SNAPSHOT"
    assert group_vars["minecraft_velocity_build"] == 601
    assert group_vars["minecraft_geyser_version"] == "2.10.1"
    assert group_vars["minecraft_geyser_build"] == 1165
    assert group_vars["minecraft_floodgate_version"] == "2.2.5"
    assert group_vars["minecraft_floodgate_build"] == 132
    assert group_vars["minecraft_viaversion_version"] == "5.9.1"
    assert group_vars["minecraft_floodgate_username_prefix"] == "."
    assert_no_latest_aliases(group_vars)


def test_minecraft_group_vars_do_not_configure_allowed_players_file():
    group_vars = load_yaml("infra/ansible/inventory/prod/group_vars/minecraft.yml")

    assert "minecraft_allowed_players_file" not in group_vars


def test_minecraft_checksum_pins_have_expected_digest_lengths():
    group_vars = load_yaml("infra/ansible/inventory/prod/group_vars/minecraft.yml")

    assert_digest_pin(group_vars, "minecraft_paper_sha256", 64)
    assert_digest_pin(group_vars, "minecraft_velocity_sha256", 64)
    assert_digest_pin(group_vars, "minecraft_geyser_velocity_sha256", 64)
    assert_digest_pin(group_vars, "minecraft_floodgate_velocity_sha256", 64)
    assert_digest_pin(group_vars, "minecraft_viaversion_sha512", 128)


def test_minecraft_allowed_players_file_is_not_source_controlled():
    assert not (REPO_ROOT / "apps/minecraft/allowed-players.yml").exists()
