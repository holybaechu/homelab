#!/usr/bin/env python3
"""Backport message-scoped auto-thread queueing to pinned Discord 2026.7.1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile


EXPECTED_VERSION = "2026.7.1"
EXPECTED_ORIGINAL_SHA256 = "4e55e1f4f4e5b6c977a75885ac88c75379ab860479d55e9d16ca990abcc19c1e"
EXPECTED_PATCHED_SHA256 = "d337d75c832c1a8aa8e82823c67080580175b3a0d60f9a122e28179bb9958439"
OLD_QUEUE = "runQueue.enqueue(job.queueKey"
NEW_QUEUE = "runQueue.enqueue(job.payload.message.id"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def patch_bundle(path: Path) -> bool:
    original = path.read_bytes()
    original_hash = sha256(original)
    if original_hash == EXPECTED_PATCHED_SHA256:
        return False
    if original_hash != EXPECTED_ORIGINAL_SHA256:
        raise SystemExit(f"refusing unexpected Discord bundle hash: {original_hash}")

    text = original.decode("utf-8")
    if text.count(OLD_QUEUE) != 1 or NEW_QUEUE in text:
        raise SystemExit("refusing Discord bundle with unexpected queue markers")
    patched = text.replace(OLD_QUEUE, NEW_QUEUE, 1).encode("utf-8")
    patched_hash = sha256(patched)
    if patched_hash != EXPECTED_PATCHED_SHA256:
        raise SystemExit(f"patched Discord bundle hash mismatch: {patched_hash}")

    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(patched)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, path.stat().st_mode & 0o777)
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("projects_root", type=Path)
    args = parser.parse_args()

    roots: list[Path] = []
    for manifest in sorted(
        args.projects_root.glob("*/node_modules/@openclaw/discord/package.json")
    ):
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        if metadata.get("version") == EXPECTED_VERSION:
            roots.append(manifest.parent)
    if not roots:
        raise SystemExit(f"no installed Discord {EXPECTED_VERSION} package found")

    changed = False
    for root in roots:
        candidates = [
            path
            for path in sorted(root.glob("dist/message-handler-*.js"))
            if OLD_QUEUE in path.read_text(encoding="utf-8")
            or NEW_QUEUE in path.read_text(encoding="utf-8")
        ]
        if len(candidates) != 1:
            raise SystemExit(
                f"expected one Discord message-handler queue bundle in {root}, "
                f"found {len(candidates)}"
            )
        changed = patch_bundle(candidates[0]) or changed

    print("changed" if changed else "unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
