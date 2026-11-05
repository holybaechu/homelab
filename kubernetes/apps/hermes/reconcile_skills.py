"""Apply small declarative edits to existing skills without replacing custom bodies."""

import argparse
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


def plan_edits(skills_dir: Path, patches: list[dict]) -> dict[Path, str]:
    skills_dir = skills_dir.resolve()
    changes = {}
    for patch in patches:
        relative = Path(patch["path"])
        path = (skills_dir / relative).resolve()
        if relative.is_absolute() or relative.name != "SKILL.md" or not path.is_relative_to(skills_dir):
            raise ValueError(f"Invalid skill target: {relative}")
        original = path.read_bytes().decode("utf-8")
        updated = original
        for number, replacement in enumerate(patch["replacements"], 1):
            old, new = replacement["old"], replacement["new"]
            if not old or not new:
                raise ValueError(f"{path}: replacement {number} is empty")
            applied = updated.count(new)
            if applied == 1 and old not in updated.replace(new, "", 1):
                continue
            if applied or updated.count(old) != 1:
                raise ValueError(f"{path}: replacement {number} has a missing or ambiguous anchor")
            updated = updated.replace(old, new, 1)
        if updated != original:
            changes[path] = updated
    return changes


def publish(path: Path, content: str):
    metadata = path.stat()
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content.encode("utf-8"))
        if os.name == "posix":
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
        temporary.chmod(stat.S_IMODE(metadata.st_mode))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-dir", type=Path, required=True)
    parser.add_argument("--patches", type=Path, required=True)
    args = parser.parse_args()
    completed = 0
    try:
        patches = json.loads(args.patches.read_text(encoding="utf-8"))
        changes = plan_edits(args.skills_dir, patches)
        for path, content in changes.items():
            publish(path, content)
            completed += 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Skill overrides failed after {completed} update(s): {error}", file=sys.stderr)
        return 1
    print(f"Updated {completed} skill file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
