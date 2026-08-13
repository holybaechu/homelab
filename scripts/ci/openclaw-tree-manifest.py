#!/usr/bin/env python3
"""Hash a filesystem tree by relative path, entry type, link target, and content."""

from __future__ import annotations

import hashlib
import os
import posixpath
from pathlib import Path
import sys


def manifest(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("manifest root must be a real directory")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
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
    if len(sys.argv) != 2:
        raise SystemExit("usage: openclaw-tree-manifest.py DIRECTORY")
    print(manifest(Path(sys.argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
