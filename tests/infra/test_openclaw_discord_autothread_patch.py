import hashlib
import importlib.util

import pytest

from tests.helpers import REPO_ROOT


PATCHER_PATH = (
    REPO_ROOT
    / "infra/openclaw/gateway/patches/patch-openclaw-discord-autothread-queue.py"
)


def load_patcher():
    spec = importlib.util.spec_from_file_location(
        "openclaw_discord_autothread_patcher", PATCHER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_discord_patcher_uses_message_identity_once_and_is_idempotent(tmp_path):
    patcher = load_patcher()
    original = b"before runQueue.enqueue(job.queueKey, callback) after\n"
    expected = original.replace(
        patcher.OLD_QUEUE.encode(), patcher.NEW_QUEUE.encode(), 1
    )
    patcher.EXPECTED_ORIGINAL_SHA256 = digest(original)
    patcher.EXPECTED_PATCHED_SHA256 = digest(expected)
    bundle = tmp_path / "message-handler-fixture.js"
    bundle.write_bytes(original)

    assert patcher.patch_bundle(bundle) is True
    assert bundle.read_bytes() == expected
    assert patcher.patch_bundle(bundle) is False


def test_discord_patcher_rejects_an_unpinned_bundle(tmp_path):
    patcher = load_patcher()
    bundle = tmp_path / "message-handler-unexpected.js"
    bundle.write_text("unexpected", encoding="utf-8")

    with pytest.raises(SystemExit, match="refusing unexpected Discord bundle hash"):
        patcher.patch_bundle(bundle)
