import importlib.util
import json
import re

import pytest

from tests.helpers import REPO_ROOT


CLASSIFIER_PATH = (
    REPO_ROOT
    / "infra/ansible/roles/openclaw_native/files/classify_openclaw_journal.py"
)


def load_classifier():
    spec = importlib.util.spec_from_file_location(
        "openclaw_journal_classifier", CLASSIFIER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CLASSIFIER = load_classifier()


def journal_bytes(*messages):
    return b"".join(
        json.dumps({"MESSAGE": message}).encode("utf-8") + b"\n"
        for message in messages
    )


@pytest.mark.parametrize(
    ("message", "category"),
    [
        (
            "Gateway failed to start: startup failed: required secrets are unavailable",
            "credential-unavailable",
        ),
        ("Gateway failed to start: gateway already running", "gateway-lock-conflict"),
        ("Failed to acquire gateway lock", "gateway-lock-access"),
        ("another gateway instance is already listening", "listener-address-in-use"),
        ("bind failed with EADDRINUSE", "listener-address-in-use"),
        ("bind failed with EADDRNOTAVAIL", "listener-address-unavailable"),
        (
            "gateway HTTP server failed to start: EACCES permission denied",
            "listener-bind-permission",
        ),
        ("failed to bind gateway socket", "http-listener-startup"),
        ("Gateway startup failed: EACCES", "filesystem-permission"),
        ("Gateway failed to start: ENOENT", "required-path-missing"),
        ("Gateway startup failed: SQLITE_CANTOPEN", "sqlite-startup"),
        (
            "[openclaw] Reason: OpenClaw startup migrations did not complete "
            "cleanly; refusing to report the gateway ready.",
            "startup-migration",
        ),
        (
            "[openclaw] Reason: OpenClaw startup migration lease is still active",
            "startup-migration",
        ),
        (
            "[openclaw] Reason: OpenClaw startup migrations were skipped",
            "startup-migration",
        ),
        ("[openclaw] Reason: ERR_MODULE_NOT_FOUND", "module-loader"),
        ("[openclaw] Reason: Cannot find package openclaw-addon", "module-loader"),
        (
            "[openclaw] Reason: The requested module 'addon' does not provide "
            "an export named 'default'",
            "module-loader",
        ),
        ("[openclaw] Reason: Module did not self-register", "module-loader"),
        (
            "[SECRETS_RELOADER_DEGRADED] SecretProviderResolutionError: "
            "provider unavailable",
            "credential-unavailable",
        ),
        ("[openclaw] Reason: missing-package-json", "plugin-bootstrap"),
        ("Refusing to start gateway on non-loopback without auth", "config-startup-guard"),
        ("gateway.bind=custom requires a custom host", "config-startup-guard"),
        ("gateway bind=custom requested without a host", "config-startup-guard"),
        (
            "gateway bind=loopback resolved to non-loopback host",
            "config-startup-guard",
        ),
        ("refusing to bind gateway to an unsafe address", "config-startup-guard"),
        ("Plugin bootstrap error", "plugin-bootstrap"),
        ("[openclaw] Could not start the CLI.", "uncategorized-application-error"),
        ("Gateway failed to start: unknown failure", "uncategorized-application-error"),
    ],
)
def test_classifier_emits_one_primary_category_for_known_messages(message, category):
    counts = CLASSIFIER.classify_journal_bytes(journal_bytes(message))

    assert counts["journal-records"] == 1
    assert counts[category] == 1
    assert sum(counts[key] for key in CLASSIFIER.OUTPUT_KEYS[3:]) == 1


def test_classifier_never_echoes_hostile_message_or_secret_material():
    token = "a" * 64
    hostile = (
        "Gateway failed to start: unknown fatal issue\n"
        f"OPENCLAW_GATEWAY_TOKEN={token} "
        '{"gateway":{"auth":{"token":"stolen"}}} '
        "/run/credentials/openclaw_gateway_token /secret/path \x1b[31m"
    )

    rendered = CLASSIFIER.render_counts(
        CLASSIFIER.classify_journal_bytes(journal_bytes(hostile))
    )

    assert "uncategorized-application-error=1\n" in rendered
    for forbidden in (
        token,
        "OPENCLAW_GATEWAY_TOKEN",
        "stolen",
        "/run/credentials",
        "/secret/path",
        "\x1b",
        "{",
    ):
        assert forbidden not in rendered
    assert rendered.splitlines() == [
        f"{key}={1 if key in {'journal-records', 'uncategorized-application-error'} else 0}"
        for key in CLASSIFIER.OUTPUT_KEYS
    ]


def test_classifier_ignores_bad_json_and_non_string_messages_safely():
    raw = b'not-json\n{"MESSAGE":123}\n[]\n' + journal_bytes("informational")
    counts = CLASSIFIER.classify_journal_bytes(raw)

    assert counts["journal-records"] == 1
    assert sum(counts[key] for key in CLASSIFIER.OUTPUT_KEYS[3:]) == 0


def test_classifier_empty_and_query_failure_outputs_keep_exact_schema():
    empty = CLASSIFIER.render_counts(CLASSIFIER.classify_journal_bytes(b""))
    failed = CLASSIFIER.render_counts(
        CLASSIFIER.classify_journal_bytes(
            journal_bytes("Gateway failed to start: secret text"),
            query_failed=True,
        )
    )

    assert empty == "".join(f"{key}=0\n" for key in CLASSIFIER.OUTPUT_KEYS)
    assert failed == "".join(
        f"{key}={1 if key == 'journal-query-failed' else 0}\n"
        for key in CLASSIFIER.OUTPUT_KEYS
    )


def test_journal_query_start_failure_emits_only_the_fixed_failure_schema():
    def fail_to_start(*args, **kwargs):
        raise OSError("hostile path /secret and token " + ("b" * 64))

    raw, query_failed, truncated = CLASSIFIER.read_bounded_journal(
        popen_factory=fail_to_start
    )
    rendered = CLASSIFIER.render_counts(
        CLASSIFIER.classify_journal_bytes(
            raw,
            query_failed=query_failed,
            journal_truncated=truncated,
        )
    )

    assert rendered == "".join(
        f"{key}={1 if key == 'journal-query-failed' else 0}\n"
        for key in CLASSIFIER.OUTPUT_KEYS
    )
    assert "/secret" not in rendered
    assert "b" * 64 not in rendered


def test_journal_query_timeout_emits_only_the_fixed_failure_schema():
    class FakeStdout:
        def fileno(self):
            return 42

        def close(self):
            return None

    class FakeProcess:
        def __init__(self):
            self.stdout = FakeStdout()
            self.killed = False

        def kill(self):
            self.killed = True

        def wait(self, timeout):
            assert timeout == 1
            return -9

        def poll(self):
            return None

    process = FakeProcess()
    clock = iter((0.0, 11.0))
    raw, query_failed, truncated = CLASSIFIER.read_bounded_journal(
        popen_factory=lambda *args, **kwargs: process,
        monotonic=lambda: next(clock),
        set_blocking_fn=lambda *args: None,
    )
    counts = CLASSIFIER.classify_journal_bytes(
        raw,
        query_failed=query_failed,
        journal_truncated=truncated,
    )

    assert process.killed is True
    assert counts == {
        key: 1 if key == "journal-query-failed" else 0
        for key in CLASSIFIER.OUTPUT_KEYS
    }


def test_classifier_bounds_record_count_and_oversize_input():
    too_many = journal_bytes(*(["Gateway failed to start: unknown"] * 201))
    record_counts = CLASSIFIER.classify_journal_bytes(too_many)

    assert record_counts["journal-truncated"] == 1
    assert record_counts["journal-records"] == 200
    assert record_counts["uncategorized-application-error"] == 200

    oversized_message = "Gateway failed to start: " + (
        "x" * CLASSIFIER.MAX_JOURNAL_BYTES
    )
    byte_counts = CLASSIFIER.classify_journal_bytes(journal_bytes(oversized_message))
    assert byte_counts["journal-truncated"] == 1
    assert byte_counts["journal-records"] == 0


def test_classifier_output_is_exact_ascii_bounded_counter_grammar():
    rendered = CLASSIFIER.render_counts(
        CLASSIFIER.classify_journal_bytes(
            journal_bytes("Gateway failed to start: SQLITE_BUSY")
        )
    )
    lines = rendered.splitlines()

    assert len(lines) == len(CLASSIFIER.OUTPUT_KEYS)
    assert [line.split("=", 1)[0] for line in lines] == list(CLASSIFIER.OUTPUT_KEYS)
    assert all(re.fullmatch(r"[a-z][a-z0-9-]*=[0-9]{1,3}", line) for line in lines)
    rendered.encode("ascii")


def test_classifier_uses_only_the_bounded_reverse_json_journal_query():
    source = CLASSIFIER_PATH.read_text(encoding="utf-8")

    assert CLASSIFIER.JOURNAL_COMMAND == (
        "/usr/bin/journalctl",
        "--unit=openclaw-gateway.service",
        "--boot=0",
        "--lines=200",
        "--reverse",
        "--output=json",
        "--no-pager",
    )
    assert CLASSIFIER.MAX_JOURNAL_BYTES == 262144
    assert CLASSIFIER.MAX_JOURNAL_RECORDS == 200
    assert CLASSIFIER.JOURNAL_TIMEOUT_SECONDS == 10
    assert "stderr=subprocess.DEVNULL" in source
    assert "MAX_JOURNAL_BYTES + 1" not in source
