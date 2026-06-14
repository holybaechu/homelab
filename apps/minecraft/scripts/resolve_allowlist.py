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


def canonical_uuid(value: str) -> str:
    return dashed_uuid(value)


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


def parse_bedrock_spec(spec: str) -> tuple[str, str | None]:
    if "=" not in spec:
        return spec, None
    gamertag, uuid = spec.split("=", 1)
    return gamertag, uuid


def resolve_java_player(name: str) -> dict:
    try:
        profile = fetch_json(MOJANG_PROFILE_URL.format(name=quote(name, safe="")))
        return {"uuid": dashed_uuid(profile["id"]), "name": profile["name"]}
    except Exception as exc:
        raise RuntimeError(f"failed to resolve Java player {name}: {exc}") from exc


def resolve_bedrock_player(gamertag: str, prefix: str, uuid: str | None = None) -> dict:
    try:
        prefixed_name = normalize_bedrock_name(gamertag, prefix)
        if uuid is not None:
            return {"uuid": canonical_uuid(uuid), "name": prefixed_name}
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


def write_json_file(path: Path, entries: list[dict]) -> str:
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


def write_whitelist(path: Path, entries: list[dict]) -> str:
    return write_json_file(path, entries)


def write_ops(
    path: Path,
    entries: list[dict],
    level: int = 4,
    bypasses_player_limit: bool = False,
) -> str:
    operators = [
        {
            "uuid": entry["uuid"],
            "name": entry["name"],
            "level": level,
            "bypassesPlayerLimit": bypasses_player_limit,
        }
        for entry in entries
    ]
    return write_json_file(path, operators)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve Java and Floodgate Bedrock players into Paper access files."
    )
    parser.add_argument("--java", action="append", default=[], help="Java username")
    parser.add_argument(
        "--java-op",
        action="append",
        default=[],
        help="Java username to write to ops.json",
    )
    parser.add_argument(
        "--bedrock",
        action="append",
        default=[],
        help="Bedrock gamertag, or gamertag=uuid to bypass GeyserMC UUID lookup",
    )
    parser.add_argument(
        "--bedrock-op",
        action="append",
        default=[],
        help="Bedrock gamertag, or gamertag=uuid to write to ops.json",
    )
    parser.add_argument("--bedrock-prefix", default=".", help="Floodgate username prefix")
    parser.add_argument("--output", required=True, help="Path to whitelist.json")
    parser.add_argument("--ops-output", help="Path to ops.json")
    parser.add_argument("--op-level", type=int, default=4, help="Paper operator level")
    parser.add_argument(
        "--op-bypasses-player-limit",
        action="store_true",
        help="Allow ops to bypass the player limit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    entries = []
    operator_entries = []

    try:
        entries.extend(resolve_java_player(name) for name in args.java)
        entries.extend(
            resolve_bedrock_player(gamertag, args.bedrock_prefix, uuid)
            for gamertag, uuid in (parse_bedrock_spec(spec) for spec in args.bedrock)
        )
        operator_entries.extend(resolve_java_player(name) for name in args.java_op)
        operator_entries.extend(
            resolve_bedrock_player(gamertag, args.bedrock_prefix, uuid)
            for gamertag, uuid in (
                parse_bedrock_spec(spec) for spec in args.bedrock_op
            )
        )
        results = [write_whitelist(Path(args.output), entries)]
        if args.ops_output:
            results.append(
                write_ops(
                    Path(args.ops_output),
                    operator_entries,
                    level=args.op_level,
                    bypasses_player_limit=args.op_bypasses_player_limit,
                )
            )
    except Exception as exc:
        print(
            "failed to resolve Minecraft allowlist. For Bedrock players, make sure "
            "the gamertag is known to the GeyserMC UUID API cache or provide the "
            "correct Floodgate UUID before enabling the whitelist.",
            file=sys.stderr,
        )
        print(str(exc), file=sys.stderr)
        return 1

    print("changed" if "changed" in results else "unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
