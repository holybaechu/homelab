#!/usr/bin/env python3
"""Hash a filesystem tree by relative path, entry type, link target, and content."""

from __future__ import annotations

import hashlib
import os
import posixpath
from pathlib import Path
import sys


DOCKER_GENERATED_PLUGIN_SKILLS = {
    "browser-automation": "/app/dist/extensions/browser/skills/browser-automation",
    "canvas": "/app/dist/extensions/canvas/skills/canvas",
}
DOCKER_GENERATED_PLUGIN_SKILLS_DIR = "plugin-skills"


def validate_docker_generated_plugin_skills(root: Path, *, allow_absent: bool) -> None:
    """Accept only the exact disposable links emitted by the pinned image."""
    plugin_skills = root / DOCKER_GENERATED_PLUGIN_SKILLS_DIR
    if not plugin_skills.exists() and not plugin_skills.is_symlink():
        if allow_absent:
            return
        raise ValueError("Docker-generated plugin-skills directory is absent")
    if plugin_skills.is_symlink() or not plugin_skills.is_dir():
        raise ValueError("Docker-generated plugin-skills must be a real directory")

    actual_entries = {entry.name for entry in plugin_skills.iterdir()}
    expected_entries = set(DOCKER_GENERATED_PLUGIN_SKILLS)
    if actual_entries != expected_entries:
        raise ValueError("Docker-generated plugin-skills entries differ from the allowlist")

    for name, expected_target in DOCKER_GENERATED_PLUGIN_SKILLS.items():
        entry = plugin_skills / name
        if not entry.is_symlink() or os.readlink(entry) != expected_target:
            raise ValueError(f"Docker-generated plugin-skills link is invalid: {name}")


def manifest(
    root: Path,
    *,
    exclude_docker_generated_plugin_skills: bool = False,
    allow_absent_docker_generated_plugin_skills: bool = False,
) -> str:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("manifest root must be a real directory")
    if exclude_docker_generated_plugin_skills:
        validate_docker_generated_plugin_skills(
            root, allow_absent=allow_absent_docker_generated_plugin_skills
        )
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if exclude_docker_generated_plugin_skills and (
            relative == DOCKER_GENERATED_PLUGIN_SKILLS_DIR
            or relative.startswith(f"{DOCKER_GENERATED_PLUGIN_SKILLS_DIR}/")
        ):
            continue
        digest.update(relative.encode("utf-8") + b"\0")
        if path.is_symlink():
            target = os.readlink(path)
            if posixpath.isabs(target):
                raise ValueError(f"absolute symlink target is forbidden: {relative}")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(relative), target))
            if resolved == ".." or resolved.startswith("../"):
                raise ValueError(f"root-escaping symlink target is forbidden: {relative}")
            digest.update(b"L" + target.encode("utf-8") + b"\0")
        elif path.is_file():
            content = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    content.update(chunk)
            digest.update(b"F" + content.digest())
        elif path.is_dir():
            digest.update(b"D")
        else:
            raise ValueError(f"unsupported filesystem entry: {relative}")
    return digest.hexdigest()


def main() -> int:
    exclude_generated = False
    arguments = sys.argv[1:]
    if arguments[:1] == ["--exclude-docker-generated-plugin-skills"]:
        exclude_generated = True
        arguments = arguments[1:]
    allow_absent_generated = False
    if arguments[:1] == ["--allow-absent-docker-generated-plugin-skills"]:
        allow_absent_generated = True
        arguments = arguments[1:]
    if allow_absent_generated and not exclude_generated:
        raise SystemExit(
            "--allow-absent-docker-generated-plugin-skills requires the exclusion flag"
        )
    if len(arguments) != 1:
        raise SystemExit(
            "usage: openclaw-tree-manifest.py "
            "[--exclude-docker-generated-plugin-skills "
            "[--allow-absent-docker-generated-plugin-skills]] DIRECTORY"
        )
    print(
        manifest(
            Path(arguments[0]),
            exclude_docker_generated_plugin_skills=exclude_generated,
            allow_absent_docker_generated_plugin_skills=allow_absent_generated,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
