#!/usr/bin/python3
"""Emit only bounded, fixed-schema OpenClaw Gateway journal counters."""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
import time
from collections.abc import Mapping


MAX_JOURNAL_BYTES = 256 * 1024
MAX_JOURNAL_RECORDS = 200
JOURNAL_TIMEOUT_SECONDS = 10

OUTPUT_KEYS = (
    "journal-query-failed",
    "journal-truncated",
    "journal-records",
    "credential-unavailable",
    "gateway-lock-conflict",
    "gateway-lock-access",
    "listener-address-in-use",
    "listener-address-unavailable",
    "listener-bind-permission",
    "http-listener-startup",
    "filesystem-permission",
    "required-path-missing",
    "sqlite-startup",
    "startup-migration",
    "module-loader",
    "config-startup-guard",
    "plugin-bootstrap",
    "uncategorized-application-error",
)

JOURNAL_COMMAND = (
    "/usr/bin/journalctl",
    "--unit=openclaw-gateway.service",
    "--boot=0",
    "--lines=200",
    "--reverse",
    "--output=json",
    "--no-pager",
)

_ERROR_LIKE = re.compile(
    r"gateway failed to start:|gateway startup failed:|startup failed:|"
    r"\[openclaw\] could not start the cli\.|\[openclaw\] reason:|"
    r"uncaught exception|unhandled rejection|\bfatal\b",
    re.IGNORECASE,
)
_EACCES_OR_EPERM = re.compile(r"\b(?:EACCES|EPERM)\b", re.IGNORECASE)
_ENOENT = re.compile(r"\bENOENT\b", re.IGNORECASE)
_EADDRINUSE = re.compile(r"\bEADDRINUSE\b", re.IGNORECASE)
_EADDRUNAVAILABLE = re.compile(
    r"\b(?:EADDRNOTAVAIL|EAFNOSUPPORT)\b", re.IGNORECASE
)
_SQLITE = re.compile(r"\bSQLITE_|\bsqlite\b|\bdatabase\b", re.IGNORECASE)
_MODULE_LOADER = re.compile(
    r"\bERR_(?:MODULE_NOT_FOUND|DLOPEN_FAILED)\b|"
    r"cannot find (?:package|module)|invalid ELF header|"
    r"does not provide an export named|named export .* not found|"
    r"module did not self-register",
    re.IGNORECASE,
)


def empty_counts() -> dict[str, int]:
    return {key: 0 for key in OUTPUT_KEYS}


def classify_message(message: str) -> str | None:
    """Return one primary category without ever returning message content."""
    lowered = message.lower()
    error_like = bool(_ERROR_LIKE.search(message))
    listener_startup = (
        "failed to bind gateway socket" in lowered
        or "gateway http server failed to start" in lowered
        or "missing gateway http server for bind host" in lowered
    )
    permission_error = bool(_EACCES_OR_EPERM.search(message)) or (
        "permission denied" in lowered
    )

    if (
        "startup failed: required secrets are unavailable" in lowered
        or "[secrets_reloader_degraded]" in lowered
        or "secretproviderresolutionerror" in lowered
    ):
        return "credential-unavailable"
    if "gateway already running" in lowered:
        return "gateway-lock-conflict"
    if "failed to acquire gateway lock" in lowered:
        return "gateway-lock-access"
    if (
        "another gateway instance is already listening" in lowered
        or _EADDRINUSE.search(message)
    ):
        return "listener-address-in-use"
    if _EADDRUNAVAILABLE.search(message):
        return "listener-address-unavailable"
    if listener_startup and permission_error:
        return "listener-bind-permission"
    if listener_startup:
        return "http-listener-startup"
    if error_like and permission_error:
        return "filesystem-permission"
    if error_like and (
        _ENOENT.search(message) or "no such file or directory" in lowered
    ):
        return "required-path-missing"
    if error_like and _SQLITE.search(message):
        return "sqlite-startup"
    if _MODULE_LOADER.search(message):
        return "module-loader"
    if any(
        marker in lowered
        for marker in (
            "openclaw startup migrations did not complete cleanly",
            "openclaw startup migrations are already running",
            "openclaw startup migrations were skipped",
            "openclaw startup migration lease",
        )
    ):
        return "startup-migration"
    if (
        "refusing to start gateway" in lowered
        or "refusing to bind gateway to" in lowered
        or "invalid --bind" in lowered
        or "custom bind requires" in lowered
        or "gateway.bind=custom requires" in lowered
        or "gateway bind=custom requested" in lowered
        or "gateway bind=loopback resolved to non-loopback host" in lowered
        or ("non-loopback" in lowered and "auth" in lowered)
        or "allowedorigins" in lowered
        or "trustedproxies" in lowered
        or "trusted proxies" in lowered
    ):
        return "config-startup-guard"
    if (
        "plugin" in lowered
        and any(marker in lowered for marker in ("failed", "failure", "error"))
    ) or any(
        marker in lowered
        for marker in (
            "missing-package-dir",
            "missing-package-json",
            "invalid-package-json",
            "missing-package-entry",
            "missing-bundle-manifest",
            "invalid-bundle-manifest",
        )
    ):
        return "plugin-bootstrap"
    if error_like:
        return "uncategorized-application-error"
    return None


def classify_journal_bytes(
    raw: bytes,
    *,
    query_failed: bool = False,
    journal_truncated: bool = False,
) -> dict[str, int]:
    counts = empty_counts()
    if query_failed:
        counts["journal-query-failed"] = 1
        return counts

    if len(raw) > MAX_JOURNAL_BYTES:
        raw = raw[:MAX_JOURNAL_BYTES]
        journal_truncated = True

    lines = raw.splitlines()
    if len(lines) > MAX_JOURNAL_RECORDS:
        lines = lines[:MAX_JOURNAL_RECORDS]
        journal_truncated = True
    counts["journal-truncated"] = int(journal_truncated)

    for line in lines:
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(record, Mapping):
            continue
        message = record.get("MESSAGE")
        if not isinstance(message, str):
            continue
        counts["journal-records"] += 1
        category = classify_message(message)
        if category is not None:
            counts[category] += 1
    return counts


def render_counts(counts: Mapping[str, int]) -> str:
    return "".join(f"{key}={counts[key]}\n" for key in OUTPUT_KEYS)


def read_bounded_journal(
    *,
    popen_factory=subprocess.Popen,
    monotonic=time.monotonic,
    select_fn=select.select,
    read_fn=os.read,
    set_blocking_fn=os.set_blocking,
) -> tuple[bytes, bool, bool]:
    """Read stdout incrementally so journal data never exceeds the byte cap."""
    try:
        process = popen_factory(
            JOURNAL_COMMAND,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        return b"", True, False

    assert process.stdout is not None
    output = bytearray()
    query_failed = False
    truncated = False
    deadline = monotonic() + JOURNAL_TIMEOUT_SECONDS
    stdout_fd = process.stdout.fileno()
    set_blocking_fn(stdout_fd, False)
    return_code = None

    try:
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                query_failed = True
                process.kill()
                break
            readable, _, _ = select_fn(
                [stdout_fd], [], [], min(remaining, 0.25)
            )
            if readable:
                chunk = read_fn(
                    stdout_fd,
                    min(65536, MAX_JOURNAL_BYTES - len(output)),
                )
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) >= MAX_JOURNAL_BYTES:
                    truncated = True
                    process.kill()
                    break
            elif process.poll() is not None:
                break
        try:
            return_code = process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)
            query_failed = True
        if not truncated and return_code is not None and return_code != 0:
            query_failed = True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass
        query_failed = True
    finally:
        process.stdout.close()

    return bytes(output), query_failed, truncated


def main() -> int:
    raw, query_failed, truncated = read_bounded_journal()
    counts = classify_journal_bytes(
        raw,
        query_failed=query_failed,
        journal_truncated=truncated,
    )
    sys.stdout.write(render_counts(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
