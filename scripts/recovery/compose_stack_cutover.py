#!/usr/bin/env python3
"""One-time Compose cutover, explicit empty-host initialization, and drill.

This recovery-only command consumes already staged legacy releases on the
Docker host, or a canonical root-owned reconstruction approval on a genuinely
empty host. Routine deployments use deploy_compose_release.py and never enter
this transition state machine.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ci.deploy_compose_release import (  # noqa: E402
    ComposeReleaseDeployer,
    DeploymentError,
    ImageApproval,
    ProjectSnapshot,
    Release,
    SHA_RE,
    STACK_PROJECT,
    _add_root_arguments,
    _add_t3_approval_arguments,
    _atomic_write,
    _deployer_from_args,
    _present,
    _t3_approval_from_args,
    _trusted,
)


LEGACY_PROJECTS = ("platform", "media", "code")
LEGACY_STOP_ORDER = ("code", "media", "platform")
LEGACY_RESTORE_ORDER = ("platform", "media", "code")
LEGACY_PROCESS_LIVENESS_SERVICES = frozenset(
    ("cloudflare-ddns", "copyparty", "qbittorrent")
)
MIGRATION_STATE_NAME = "stack-migration.json"
MIGRATION_LOCK_NAME = "stack-migration.lock"
EMPTY_HOST_APPROVAL_NAME = "empty-host-reconstruction.json"


@dataclass(frozen=True)
class MigrationRecord:
    active: str
    homelab: str
    legacy: Mapping[str, Mapping[str, str | None]] | None
    origin: str = "legacy-cutover"

    def to_json(self) -> str:
        if self.origin == "empty-host-reconstruction":
            payload = {
                "version": 2,
                "active": self.active,
                "homelab": self.homelab,
                "origin": self.origin,
                "legacy": {},
            }
        else:
            payload = {
                "version": 1,
                "active": self.active,
                "homelab": self.homelab,
                "legacy": self.legacy,
            }
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"


def _parse_migration_record(payload: object) -> MigrationRecord:
    if (
        isinstance(payload, dict)
        and set(payload) == {"version", "active", "homelab", "origin", "legacy"}
        and payload["version"] == 2
    ):
        active = payload["active"]
        homelab = payload["homelab"]
        if (
            active != STACK_PROJECT
            or not isinstance(homelab, str)
            or not SHA_RE.fullmatch(homelab)
            or payload["origin"] != "empty-host-reconstruction"
            or payload["legacy"] != {}
        ):
            raise DeploymentError("empty-host reconstruction record is invalid")
        return MigrationRecord(
            active=active,
            homelab=homelab,
            legacy=None,
            origin="empty-host-reconstruction",
        )
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "active",
        "homelab",
        "legacy",
    }:
        raise DeploymentError("stack migration record has unexpected fields")
    active = payload["active"]
    homelab = payload["homelab"]
    legacy = payload["legacy"]
    if (
        payload["version"] != 1
        or active not in {"homelab", "legacy"}
        or not isinstance(homelab, str)
        or not SHA_RE.fullmatch(homelab)
        or not isinstance(legacy, dict)
        or set(legacy) != set(LEGACY_PROJECTS)
    ):
        raise DeploymentError("stack migration record identity is invalid")
    normalized: dict[str, dict[str, str | None]] = {}
    for project in LEGACY_PROJECTS:
        item = legacy[project]
        if not isinstance(item, dict) or set(item) != {"good", "previous"}:
            raise DeploymentError("stack migration legacy state is invalid")
        good, previous = item["good"], item["previous"]
        if (
            not isinstance(good, str)
            or not SHA_RE.fullmatch(good)
            or (
                previous is not None
                and (not isinstance(previous, str) or not SHA_RE.fullmatch(previous))
            )
        ):
            raise DeploymentError("stack migration legacy release identity is invalid")
        normalized[project] = {"good": good, "previous": previous}
    return MigrationRecord(active=active, homelab=homelab, legacy=normalized)


def _load_migration_record(
    state_root: Path, trusted_uid: int
) -> MigrationRecord | None:
    pending = state_root / f"{MIGRATION_STATE_NAME}.pending"
    temporary = list(state_root.glob(f".{MIGRATION_STATE_NAME}.tmp-*"))
    if _present(pending) or any(_present(path) for path in temporary):
        raise DeploymentError("partial stack migration state is present")
    path = state_root / MIGRATION_STATE_NAME
    if not _present(path):
        return None
    _trusted(
        path,
        trusted_uid,
        directory=False,
        private=True,
        label="stack migration record",
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentError("stack migration record is invalid") from exc
    return _parse_migration_record(payload)


class ComposeStackMigrator:
    """One-time, reversible cutover from three projects to ``homelab``."""

    def __init__(self, deployer: ComposeReleaseDeployer) -> None:
        self.deployer = deployer

    def _lock_all(self) -> ExitStack:
        stack = ExitStack()
        try:
            stack.enter_context(
                self.deployer.lock_factory(
                    self.deployer.state_root / "locks" / MIGRATION_LOCK_NAME,
                    self.deployer.trusted_uid,
                )
            )
            for project in (*LEGACY_PROJECTS, STACK_PROJECT):
                stack.enter_context(
                    self.deployer.lock_factory(
                        self.deployer.state_root / "locks" / f"{project}.lock",
                        self.deployer.trusted_uid,
                    )
                )
            # A manual recovery command cannot observe or overwrite a killed
            # routine deployment. Reconcile its durable journal only after all
            # migration and project locks are held.
            self.deployer._reconcile_pending_transaction(STACK_PROJECT)
        except BaseException:
            stack.close()
            raise
        return stack

    @staticmethod
    def _validate_sha(sha: str) -> str:
        if sha != sha.lower() or not SHA_RE.fullmatch(sha):
            raise DeploymentError(
                "migration SHA must be exactly 40 or 64 lowercase hexadecimal characters"
            )
        return sha

    def _load_legacy(
        self, record: MigrationRecord | None
    ) -> dict[str, ProjectSnapshot]:
        if record is not None and record.legacy is None:
            raise DeploymentError(
                "empty-host reconstruction has no legacy rollback checkpoint"
            )
        snapshots: dict[str, ProjectSnapshot] = {}
        for project in LEGACY_PROJECTS:
            snapshot = self.deployer._snapshot(project, require_state=True)
            if record is not None:
                expected = record.legacy[project]
                if (
                    snapshot.release.sha != expected["good"]
                    or snapshot.previous != expected["previous"]
                ):
                    raise DeploymentError(
                        f"legacy {project} pointer/state differs from the migration record"
                    )
            snapshots[project] = snapshot
        return snapshots

    def _validate_empty_host_approval(self, sha: str, approval_file: Path) -> None:
        expected = self.deployer.state_root / EMPTY_HOST_APPROVAL_NAME
        if approval_file.absolute() != expected.absolute():
            raise DeploymentError(
                f"empty-host approval must use the canonical path: {expected}"
            )
        _trusted(
            expected,
            self.deployer.trusted_uid,
            directory=False,
            private=True,
            label="empty-host reconstruction approval",
        )
        try:
            payload = json.loads(expected.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DeploymentError("empty-host reconstruction approval is invalid") from exc
        if payload != {
            "version": 1,
            "operation": "initialize-empty-host",
            "sha": sha,
        }:
            raise DeploymentError(
                "empty-host reconstruction approval must exactly bind this release SHA"
            )

    def initialize_empty_host(
        self,
        sha: str,
        *,
        approval_file: Path,
        t3_approval: ImageApproval | None = None,
    ) -> str:
        """Explicitly initialize a reconstructed host with no legacy state."""

        sha = self._validate_sha(sha)
        self.deployer._prepare_roots()
        with self._lock_all():
            if _load_migration_record(
                self.deployer.state_root, self.deployer.trusted_uid
            ) is not None:
                raise DeploymentError("stack initialization is already recorded")
            self._validate_empty_host_approval(sha, approval_file)
            for project in LEGACY_PROJECTS:
                self.deployer._require_project_artifacts_absent(project)
            self.deployer._require_project_artifacts_absent(STACK_PROJECT)

            artifacts, candidate_source = self.deployer._select_homelab_artifacts(
                sha, t3_approval
            )
            homelab = self.deployer._stage(
                sha, STACK_PROJECT, artifacts=artifacts
            )
            services = self.deployer._preflight(homelab, pull=True)
            pointer_changed = False
            try:
                self.deployer._write_pointer(STACK_PROJECT, sha)
                pointer_changed = True
                self.deployer._activate(homelab, services, smoke=True)
                if candidate_source is not None:
                    self.deployer._promote_homelab_artifact(candidate_source)
                self.deployer._write_state(STACK_PROJECT, sha, None)
                self._write_record(
                    MigrationRecord(
                        active=STACK_PROJECT,
                        homelab=sha,
                        legacy=None,
                        origin="empty-host-reconstruction",
                    )
                )
            except BaseException as original:
                if not pointer_changed:
                    raise
                errors: list[str] = []
                try:
                    self.deployer._rollback(
                        STACK_PROJECT, failed=homelab, previous=None
                    )
                except BaseException as rollback:
                    errors.append(f"homelab rollback: {rollback}")
                try:
                    self.deployer._remove_state(STACK_PROJECT)
                except BaseException as state_cleanup:
                    errors.append(f"homelab state cleanup: {state_cleanup}")
                if errors:
                    raise DeploymentError(
                        f"empty-host initialization failed ({original}); "
                        + "; ".join(errors)
                    ) from original
                raise DeploymentError(
                    f"empty-host initialization failed and was stopped: {original}"
                ) from original
            return "initialized"

    @staticmethod
    def _legacy_payload(
        snapshots: Mapping[str, ProjectSnapshot],
    ) -> dict[str, dict[str, str | None]]:
        return {
            project: {
                "good": snapshots[project].release.sha,
                "previous": snapshots[project].previous,
            }
            for project in LEGACY_PROJECTS
        }

    def _write_record(self, record: MigrationRecord) -> None:
        path = self.deployer.state_root / MIGRATION_STATE_NAME
        if _present(path):
            _trusted(
                path,
                self.deployer.trusted_uid,
                directory=False,
                private=True,
                label="stack migration record",
            )
        _atomic_write(path, record.to_json())

    def _verify_legacy_running(
        self, snapshots: Mapping[str, ProjectSnapshot]
    ) -> None:
        for project in LEGACY_PROJECTS:
            release = snapshots[project].release
            services = self.deployer._preflight(release, pull=False)
            self.deployer._verify_running(
                release,
                services,
                smoke=True,
                process_liveness_services=LEGACY_PROCESS_LIVENESS_SERVICES,
            )

    def _verify_legacy_stopped(
        self, snapshots: Mapping[str, ProjectSnapshot]
    ) -> None:
        for project in LEGACY_PROJECTS:
            self.deployer._verify_stopped(snapshots[project].release)

    def _stop_legacy(self, snapshots: Mapping[str, ProjectSnapshot]) -> None:
        for project in LEGACY_STOP_ORDER:
            self.deployer._stop(snapshots[project].release)

    def _restore_legacy(self, snapshots: Mapping[str, ProjectSnapshot]) -> None:
        for project in LEGACY_RESTORE_ORDER:
            self.deployer._restore_snapshot(
                snapshots[project],
                process_liveness_services=LEGACY_PROCESS_LIVENESS_SERVICES,
            )

    def _remove_first_homelab_state(self) -> None:
        self.deployer._remove_state(STACK_PROJECT)
        self.deployer._remove_pointer(STACK_PROJECT)

    def _restore_inactive_homelab(self, snapshot: ProjectSnapshot) -> None:
        self.deployer._write_pointer(STACK_PROJECT, snapshot.release.sha)
        self.deployer._write_state(
            STACK_PROJECT, snapshot.release.sha, snapshot.previous
        )
        self.deployer._verify_stopped(snapshot.release)

    def _rollback_failed_migration(
        self,
        homelab: Release,
        prior_homelab: ProjectSnapshot | None,
        legacy: Mapping[str, ProjectSnapshot],
    ) -> None:
        errors: list[str] = []
        try:
            self.deployer._stop(homelab)
        except BaseException as exc:
            errors.append(f"homelab stop: {exc}")
        try:
            if prior_homelab is None:
                self._remove_first_homelab_state()
            else:
                self._restore_inactive_homelab(prior_homelab)
        except BaseException as exc:
            errors.append(f"homelab pointer/state restore: {exc}")
        for project in LEGACY_RESTORE_ORDER:
            try:
                self.deployer._restore_snapshot(
                    legacy[project],
                    process_liveness_services=LEGACY_PROCESS_LIVENESS_SERVICES,
                )
            except BaseException as exc:
                errors.append(f"legacy {project} restore: {exc}")
        if errors:
            raise DeploymentError("; ".join(errors))

    def migrate(
        self, sha: str, *, t3_approval: ImageApproval | None = None
    ) -> str:
        sha = self._validate_sha(sha)
        self.deployer._prepare_roots()
        with self._lock_all():
            record = _load_migration_record(
                self.deployer.state_root, self.deployer.trusted_uid
            )
            legacy = self._load_legacy(record)

            if record is not None and record.active == STACK_PROJECT:
                if record.homelab != sha:
                    raise DeploymentError(
                        "migration already completed; deploy later homelab releases normally"
                    )
                current = self.deployer._snapshot(STACK_PROJECT, require_state=True)
                if current.release.sha != sha:
                    raise DeploymentError(
                        "current homelab release differs from the recorded migration SHA"
                    )
                artifacts, candidate_source = self.deployer._select_homelab_artifacts(
                    sha, t3_approval
                )
                if self.deployer._load_release_artifacts(current.release) != artifacts:
                    raise DeploymentError(
                        "active homelab release has different T3 artifact metadata"
                    )
                self._verify_legacy_stopped(legacy)
                services = self.deployer._preflight(current.release, pull=False)
                self.deployer._verify_running(
                    current.release, services, smoke=True
                )
                if candidate_source is not None:
                    self.deployer._promote_homelab_artifact(candidate_source)
                return "already-active"

            if record is None:
                self.deployer._require_project_artifacts_absent(STACK_PROJECT)
                prior_homelab = None
            else:
                if record.homelab != sha:
                    raise DeploymentError(
                        "legacy drill can reactivate only its recorded homelab SHA"
                    )
                prior_homelab = self.deployer._snapshot(
                    STACK_PROJECT, require_state=True
                )
                if prior_homelab.release.sha != sha:
                    raise DeploymentError(
                        "inactive homelab release differs from the migration record"
                    )
                self.deployer._verify_stopped(prior_homelab.release)

            artifacts, candidate_source = self.deployer._select_homelab_artifacts(
                sha, t3_approval
            )
            homelab = self.deployer._stage(
                sha, STACK_PROJECT, artifacts=artifacts
            )
            services = self.deployer._preflight(homelab, pull=True)
            self._verify_legacy_running(legacy)

            stop_started = False
            try:
                stop_started = True
                self._stop_legacy(legacy)
                self.deployer._write_pointer(STACK_PROJECT, sha)
                self.deployer._activate(homelab, services, smoke=True)
                if candidate_source is not None:
                    self.deployer._promote_homelab_artifact(candidate_source)
                self.deployer._write_state(
                    STACK_PROJECT,
                    sha,
                    prior_homelab.previous if prior_homelab else None,
                )
                self._write_record(
                    MigrationRecord(
                        active=STACK_PROJECT,
                        homelab=sha,
                        legacy=self._legacy_payload(legacy),
                    )
                )
            except BaseException as original:
                if not stop_started:
                    raise
                try:
                    self._rollback_failed_migration(
                        homelab, prior_homelab, legacy
                    )
                except BaseException as rollback:
                    raise DeploymentError(
                        f"stack migration failed ({original}); rollback also failed ({rollback})"
                    ) from original
                raise DeploymentError(
                    f"stack migration failed and legacy was restored: {original}"
                ) from original
            return "activated"

    def _restore_homelab_after_failed_drill(
        self,
        homelab: ProjectSnapshot,
        legacy: Mapping[str, ProjectSnapshot],
    ) -> None:
        errors: list[str] = []
        for project in LEGACY_STOP_ORDER:
            try:
                self.deployer._stop(legacy[project].release)
            except BaseException as exc:
                errors.append(f"legacy {project} stop: {exc}")
        try:
            self.deployer._restore_snapshot(homelab)
        except BaseException as exc:
            errors.append(f"homelab restore: {exc}")
        if errors:
            raise DeploymentError("; ".join(errors))

    def rollback_to_legacy(self) -> str:
        self.deployer._prepare_roots()
        with self._lock_all():
            record = _load_migration_record(
                self.deployer.state_root, self.deployer.trusted_uid
            )
            if record is None:
                raise DeploymentError("no completed stack migration is recorded")
            legacy = self._load_legacy(record)
            homelab = self.deployer._snapshot(STACK_PROJECT, require_state=True)

            if record.active == "legacy":
                if homelab.release.sha != record.homelab:
                    raise DeploymentError(
                        "inactive homelab release differs from the migration record"
                    )
                self.deployer._verify_stopped(homelab.release)
                self._verify_legacy_running(legacy)
                return "already-legacy"

            services = self.deployer._preflight(homelab.release, pull=False)
            self.deployer._verify_running(homelab.release, services, smoke=True)
            self._verify_legacy_stopped(legacy)
            stop_started = False
            try:
                stop_started = True
                self.deployer._stop(homelab.release)
                self._restore_legacy(legacy)
                self._write_record(
                    MigrationRecord(
                        active="legacy",
                        homelab=homelab.release.sha,
                        legacy=self._legacy_payload(legacy),
                    )
                )
            except BaseException as original:
                if not stop_started:
                    raise
                try:
                    self._restore_homelab_after_failed_drill(homelab, legacy)
                except BaseException as rollback:
                    raise DeploymentError(
                        f"legacy drill failed ({original}); homelab restore also failed ({rollback})"
                    ) from original
                raise DeploymentError(
                    f"legacy drill failed and homelab was restored: {original}"
                ) from original
            return "legacy-restored"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    cutover = commands.add_parser("cutover")
    cutover.add_argument("sha")
    _add_root_arguments(cutover)
    _add_t3_approval_arguments(cutover)

    initialize = commands.add_parser("initialize-empty-host")
    initialize.add_argument("sha")
    initialize.add_argument("--approval-file", required=True, type=Path)
    _add_root_arguments(initialize)
    _add_t3_approval_arguments(initialize)

    rollback = commands.add_parser("rollback-to-legacy")
    _add_root_arguments(rollback)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        print("Compose stack recovery must run as root", file=sys.stderr)
        return 1
    deployer = _deployer_from_args(args)
    try:
        if args.command == "cutover":
            approval = _t3_approval_from_args(args)
            result = ComposeStackMigrator(deployer).migrate(
                args.sha, t3_approval=approval
            )
            print(f"Homelab stack cutover: {result} at {args.sha}")
        elif args.command == "initialize-empty-host":
            approval = _t3_approval_from_args(args)
            result = ComposeStackMigrator(deployer).initialize_empty_host(
                args.sha,
                approval_file=args.approval_file,
                t3_approval=approval,
            )
            print(f"Homelab empty-host reconstruction: {result} at {args.sha}")
        else:
            result = ComposeStackMigrator(deployer).rollback_to_legacy()
            print(f"Homelab stack rollback drill: {result}")
    except (DeploymentError, OSError) as exc:
        print(f"Compose stack recovery failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
