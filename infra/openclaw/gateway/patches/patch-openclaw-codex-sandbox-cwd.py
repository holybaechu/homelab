#!/usr/bin/env python3
"""Apply the pinned Codex exec-server PathUri cwd compatibility fix."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import tempfile


EXPECTED_ORIGINAL_SHA256 = "d681a14d3eba5af268415599f2212fc92f1687d12c125d5e54021980cdd684d5"
EXPECTED_PATCHED_SHA256 = "d99c70ea7a445d49769f24582fbe312c55934326bcafb80c3132555189e94d7c"

OLD_IMPORT = 'import { once } from "node:events";'
NEW_IMPORT = 'import { once } from "node:events";\nimport { fileURLToPath } from "node:url";'
OLD_CWD = 'const cwd = requireString(record.cwd, "cwd");'
NEW_CWD = '''const cwdUrl = new URL(requireString(record.cwd, "cwd"));
	if (cwdUrl.protocol !== "file:") throw new Error(`process cwd URI must use the file scheme, received ${cwdUrl.protocol.slice(0, -1)}.`);
	if (cwdUrl.search || cwdUrl.hash) throw new Error("process cwd file URI must not include a query or fragment.");
	const cwd = fileURLToPath(cwdUrl, { windows: false });
	if (/^\\/[A-Za-z]:(?:\\/|$)/u.test(cwd)) throw new Error("process cwd Windows file URI is not supported by the sandbox.");
	if (cwd.includes("\\0")) throw new Error("process cwd file URI must not contain a null byte.");'''


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def patch_bundle(path: Path) -> bool:
    original = path.read_bytes()
    original_hash = sha256(original)
    if original_hash == EXPECTED_PATCHED_SHA256:
        return False
    if original_hash != EXPECTED_ORIGINAL_SHA256:
        raise SystemExit(f"refusing unexpected Codex bundle hash: {original_hash}")

    text = original.decode("utf-8")
    if text.count(OLD_IMPORT) != 1 or text.count(OLD_CWD) != 1:
        raise SystemExit("refusing Codex bundle with unexpected cwd patch markers")
    patched = text.replace(OLD_IMPORT, NEW_IMPORT, 1).replace(OLD_CWD, NEW_CWD, 1).encode("utf-8")
    patched_hash = sha256(patched)
    if patched_hash != EXPECTED_PATCHED_SHA256:
        raise SystemExit(f"patched Codex bundle hash mismatch: {patched_hash}")

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
    parser.add_argument("plugin_root", type=Path)
    args = parser.parse_args()
    candidates = sorted(args.plugin_root.glob("dist/run-attempt-*.js"))
    if len(candidates) != 1:
        raise SystemExit(f"expected exactly one Codex run-attempt bundle, found {len(candidates)}")
    changed = patch_bundle(candidates[0])
    print("changed" if changed else "unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
