import hashlib
import importlib.util
from pathlib import Path

import pytest

from tests.helpers import REPO_ROOT


PATCHER_PATH = (
    REPO_ROOT
    / "infra/openclaw/gateway/patches/patch-openclaw-codex-sandbox-cwd.py"
)


def load_patcher():
    spec = importlib.util.spec_from_file_location("openclaw_codex_cwd_patcher", PATCHER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_cwd_patcher_converts_codex_file_uri_once_and_is_idempotent(tmp_path):
    patcher = load_patcher()
    original = (
        'import { once } from "node:events";\n'
        'function startProcess(record) {\n'
        '\tconst cwd = requireString(record.cwd, "cwd");\n'
        '}\n'
    ).encode()
    expected = original.decode().replace(
        patcher.OLD_IMPORT, patcher.NEW_IMPORT, 1
    ).replace(patcher.OLD_CWD, patcher.NEW_CWD, 1).encode()
    patcher.EXPECTED_ORIGINAL_SHA256 = digest(original)
    patcher.EXPECTED_PATCHED_SHA256 = digest(expected)
    bundle = tmp_path / "run-attempt-fixture.js"
    bundle.write_bytes(original)

    assert patcher.patch_bundle(bundle) is True
    assert bundle.read_bytes() == expected
    assert 'fileURLToPath(cwdUrl, { windows: false })' in bundle.read_text()
    assert patcher.patch_bundle(bundle) is False


def test_cwd_patcher_rejects_an_unpinned_bundle(tmp_path):
    patcher = load_patcher()
    bundle = tmp_path / "run-attempt-unexpected.js"
    bundle.write_text("unexpected", encoding="utf-8")

    with pytest.raises(SystemExit, match="refusing unexpected Codex bundle hash"):
        patcher.patch_bundle(bundle)
