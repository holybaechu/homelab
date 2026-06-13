#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


MOJANG_PROFILE_URL = "https://api.mojang.com/users/profiles/minecraft/{name}"
GEYSER_UUID_URL = (
    "https://api.geysermc.org/v2/utils/uuid/bedrock_or_java/{name}?prefix={prefix}"
)


def dashed_uuid(value: str) -> str:
    compact = value.replace("-", "").lower()
    if len(compact) != 32 or any(char not in "0123456789abcdef" for char in compact):
        raise ValueError(f"expected 32 hex UUID characters, got {value!r}")
    return (
        f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-"
        f"{compact[16:20]}-{compact[20:32]}"
    )


def fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "homelab-minecraft-allowlist/1.0"})
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{url} returned HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"{url} failed: {exc.reason}") from exc


def normalize_bedrock_name(name: str, prefix: str) -> str:
    return name if name.startswith(prefix) else f"{prefix}{name}"


def resolve_java_player(name: str) -> dict:
    try:
        profile = fetch_json(MOJANG_PROFILE_URL.format(name=quote(name, safe="")))
        return {"uuid": dashed_uuid(profile["id"]), "name": profile["name"]}
    except Exception as exc:
        raise RuntimeError(f"failed to resolve Java player {name}: {exc}") from exc


def resolve_bedrock_player(gamertag: str, prefix: str) -> dict:
    try:
        prefixed_name = normalize_bedrock_name(gamertag, prefix)
        profile = fetch_json(
            GEYSER_UUID_URL.format(
                name=quote(prefixed_name, safe=""),
                prefix=quote(prefix, safe=""),
            )
        )
        return {
            "uuid": dashed_uuid(profile["id"]),
            "name": profile.get("name", prefixed_name),
        }
    except Exception as exc:
        raise RuntimeError(f"failed to resolve Bedrock player {gamertag}: {exc}") from exc


def write_whitelist(path: Path, entries: list[dict]) -> str:
    content = json.dumps(entries, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return "unchanged"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            delete=False,
            dir=path.parent,
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
        temp_path.replace(path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return "changed"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve Java and Floodgate Bedrock players into Paper whitelist.json."
    )
    parser.add_argument("--java", action="append", default=[], help="Java username")
    parser.add_argument("--bedrock", action="append", default=[], help="Bedrock gamertag")
    parser.add_argument("--bedrock-prefix", default=".", help="Floodgate username prefix")
    parser.add_argument("--output", required=True, help="Path to whitelist.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    entries = []

    try:
        entries.extend(resolve_java_player(name) for name in args.java)
        entries.extend(
            resolve_bedrock_player(name, args.bedrock_prefix) for name in args.bedrock
        )
        result = write_whitelist(Path(args.output), entries)
    except Exception as exc:
        print(
            "failed to resolve Minecraft allowlist. For Bedrock players, make sure "
            "the gamertag is known to the GeyserMC UUID API cache or provide the "
            "correct Floodgate UUID before enabling the whitelist.",
            file=sys.stderr,
        )
        print(str(exc), file=sys.stderr)
        return 1

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
