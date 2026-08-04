#!/usr/bin/env python3
"""Reconcile Arcane's homelab GitOps and GitHub OIDC configuration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import PurePosixPath
import re
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEPLOY_ROLE_PERMISSIONS = [
    "gitops:list",
    "gitops:read",
    "gitops:sync",
]

MANAGED_SETTINGS = {
    "autoHealEnabled": "false",
    "autoInjectEnv": "false",
    "autoUpdate": "false",
    "defaultDeployPullPolicy": "missing",
    "lifecycleEnabled": "false",
    "pollingEnabled": "false",
    "scheduledPruneEnabled": "false",
    "vulnerabilityScanEnabled": "false",
}

ROLE_DESCRIPTION = "Least-privilege GitHub Actions role managed by homelab reconciliation."
CREDENTIAL_DESCRIPTION = "GitHub Actions workload identity managed by homelab reconciliation."
REPOSITORY_DESCRIPTION = "Public homelab source managed by homelab reconciliation."
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


class ArcaneError(RuntimeError):
    """Raised when Arcane rejects or cannot complete a requested operation."""


def api_root(base_url: str) -> str:
    """Return a validated Arcane API root for a UI or /api base URL."""

    value = base_url.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("--base-url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("--base-url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("--base-url must not contain a query or fragment")

    path = parsed.path.rstrip("/")
    if not path.endswith("/api"):
        path += "/api"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _response_error(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "request failed"

    data = payload.get("data")
    candidates = [
        payload.get("detail"),
        payload.get("message"),
        payload.get("error_description"),
        payload.get("error"),
        data.get("error") if isinstance(data, dict) else None,
        data.get("message") if isinstance(data, dict) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "request failed"


class ArcaneClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        self.root = api_root(base_url)
        self.api_key = api_key
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        payload: Any | None = None,
        *,
        retry_get: bool = True,
    ) -> Any:
        url = f"{self.root}/{path.lstrip('/')}"
        body = None
        headers = {
            "Accept": "application/json",
            "User-Agent": "homelab-arcane-reconciler/1",
            "X-Api-Key": self.api_key,
        }
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        attempts = 4 if method.upper() == "GET" and retry_get else 1
        for attempt in range(attempts):
            request = Request(url, data=body, headers=headers, method=method.upper())
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                result = json.loads(raw.decode("utf-8")) if raw else None
                if isinstance(result, dict) and result.get("success") is False:
                    raise ArcaneError(
                        f"{method.upper()} {path} failed: {_response_error(result)}"
                    )
                return result
            except HTTPError as exc:
                raw = exc.read(16_384)
                try:
                    error_payload = json.loads(raw.decode("utf-8")) if raw else None
                except (UnicodeDecodeError, json.JSONDecodeError):
                    error_payload = None
                detail = _response_error(error_payload)
                if exc.code in {502, 503, 504} and attempt + 1 < attempts:
                    time.sleep(2**attempt)
                    continue
                raise ArcaneError(
                    f"{method.upper()} {path} returned HTTP {exc.code}: {detail}"
                ) from None
            except (URLError, TimeoutError, OSError) as exc:
                if attempt + 1 < attempts:
                    time.sleep(2**attempt)
                    continue
                reason = getattr(exc, "reason", exc)
                raise ArcaneError(
                    f"{method.upper()} {path} could not reach Arcane: {reason}"
                ) from None
            except json.JSONDecodeError as exc:
                raise ArcaneError(
                    f"{method.upper()} {path} returned invalid JSON: {exc.msg}"
                ) from None

        raise AssertionError("unreachable")


def response_data(response: Any, operation: str) -> Any:
    if not isinstance(response, dict) or "data" not in response:
        raise ArcaneError(f"{operation} returned an unexpected response")
    return response["data"]


def list_data(response: Any, operation: str) -> list[dict[str, Any]]:
    data = response_data(response, operation)
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ArcaneError(f"{operation} returned an unexpected item list")
    return data


def exact_named(
    items: list[dict[str, Any]], name: str, resource: str
) -> dict[str, Any] | None:
    matches = [item for item in items if item.get("name") == name]
    if len(matches) > 1:
        raise ArcaneError(f"Arcane has multiple {resource} resources named {name!r}")
    return matches[0] if matches else None


def parse_project_specs(
    values: list[str],
    *,
    option: str = "--project",
    require_any: bool = True,
) -> list[tuple[str, str]]:
    projects: list[tuple[str, str]] = []
    seen: set[str] = set()

    for value in values:
        if "=" not in value:
            raise ValueError(f"invalid {option} {value!r}; expected NAME=COMPOSE_PATH")
        raw_name, raw_path = value.split("=", 1)
        name = raw_name.strip()
        path_text = raw_path.strip().replace("\\", "/")
        if not NAME_PATTERN.fullmatch(name):
            raise ValueError(f"invalid Arcane project name {name!r}")
        if name in seen:
            raise ValueError(f"duplicate Arcane project name {name!r}")

        path = PurePosixPath(path_text)
        if (
            not path_text
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.suffix.lower() not in {".yml", ".yaml"}
        ):
            raise ValueError(
                f"invalid compose path {path_text!r}; expected a safe relative .yml/.yaml path"
            )
        normalized = path.as_posix()
        if name.casefold() == "arcane" or normalized.casefold().startswith(
            "apps/compose/arcane/"
        ):
            raise ValueError("Arcane must not Git-sync or redeploy its own control project")

        seen.add(name)
        projects.append((name, normalized))

    if require_any and not projects:
        raise ValueError(f"at least one {option} NAME=COMPOSE_PATH is required")
    return projects


def validate_https_url(value: str, option: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{option} must be an absolute HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{option} must not contain credentials")
    if parsed.fragment:
        raise ValueError(f"{option} must not contain a fragment")
    return candidate


def ensure_settings(client: ArcaneClient, environment_id: str) -> bool:
    response = client.request("GET", f"environments/{environment_id}/settings")
    if not isinstance(response, list):
        raise ArcaneError("listing settings returned an unexpected response")

    current = {
        item.get("key"): item.get("value")
        for item in response
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }
    changes = {
        key: value for key, value in MANAGED_SETTINGS.items() if current.get(key) != value
    }
    if not changes:
        print("Arcane safety settings already match")
        return False

    client.request("PUT", f"environments/{environment_id}/settings", changes)
    print("Updated Arcane safety settings: " + ", ".join(sorted(changes)))
    return True


def ensure_role(client: ArcaneClient, role_name: str) -> tuple[dict[str, Any], bool]:
    roles = list_data(client.request("GET", "roles?limit=100"), "listing roles")
    existing = exact_named(roles, role_name, "role")
    desired = {
        "name": role_name,
        "description": ROLE_DESCRIPTION,
        "permissions": DEPLOY_ROLE_PERMISSIONS,
    }

    if existing is None:
        role = response_data(client.request("POST", "roles", desired), "creating role")
        if not isinstance(role, dict):
            raise ArcaneError("creating role returned an unexpected response")
        print(f"Created Arcane role {role_name!r}")
        return role, True

    if existing.get("builtIn") is True:
        raise ArcaneError(
            f"role {role_name!r} is built in; choose a dedicated custom role name"
        )

    changed = (
        existing.get("description") != ROLE_DESCRIPTION
        or set(existing.get("permissions") or []) != set(DEPLOY_ROLE_PERMISSIONS)
    )
    if not changed:
        print(f"Arcane role {role_name!r} already matches")
        return existing, False

    role_id = existing.get("id")
    if not isinstance(role_id, str) or not role_id:
        raise ArcaneError(f"role {role_name!r} has no usable ID")
    role = response_data(
        client.request("PUT", f"roles/{role_id}", desired), "updating role"
    )
    if not isinstance(role, dict):
        raise ArcaneError("updating role returned an unexpected response")
    print(f"Updated Arcane role {role_name!r}")
    return role, True


def ensure_credential(
    client: ArcaneClient,
    *,
    name: str,
    issuer: str,
    audience: str,
    subject: str,
    role_id: str,
    environment_id: str,
    token_ttl_seconds: int,
) -> tuple[dict[str, Any], bool]:
    credentials = list_data(
        client.request("GET", "federated-credentials?limit=100"),
        "listing federated credentials",
    )
    existing = exact_named(credentials, name, "federated credential")
    desired = {
        "name": name,
        "description": CREDENTIAL_DESCRIPTION,
        "enabled": True,
        "issuerUrl": issuer,
        "audiences": [audience],
        "subjectClaim": "sub",
        "subjectMatch": subject,
        "matchType": "exact",
        "roleId": role_id,
        "environmentId": environment_id,
        "tokenTtlSeconds": token_ttl_seconds,
    }

    if existing is None:
        credential = response_data(
            client.request("POST", "federated-credentials", desired),
            "creating federated credential",
        )
        if not isinstance(credential, dict):
            raise ArcaneError("creating federated credential returned an unexpected response")
        print(f"Created Arcane federated credential {name!r}")
        return credential, True

    comparable = dict(desired)
    comparable["audiences"] = sorted(comparable["audiences"])
    current = {key: existing.get(key) for key in desired}
    current["audiences"] = sorted(current.get("audiences") or [])
    if current == comparable:
        print(f"Arcane federated credential {name!r} already matches")
        return existing, False

    credential_id = existing.get("id")
    if not isinstance(credential_id, str) or not credential_id:
        raise ArcaneError(f"federated credential {name!r} has no usable ID")
    credential = response_data(
        client.request("PUT", f"federated-credentials/{credential_id}", desired),
        "updating federated credential",
    )
    if not isinstance(credential, dict):
        raise ArcaneError("updating federated credential returned an unexpected response")
    print(f"Updated Arcane federated credential {name!r}")
    return credential, True


def ensure_repository(
    client: ArcaneClient, name: str, url: str
) -> tuple[dict[str, Any], bool]:
    repositories = list_data(
        client.request("GET", "customize/git-repositories?limit=100"),
        "listing Git repositories",
    )
    existing = exact_named(repositories, name, "Git repository")
    desired = {
        "name": name,
        "url": url,
        "authType": "none",
        "description": REPOSITORY_DESCRIPTION,
        "enabled": True,
    }

    if existing is None:
        repository = response_data(
            client.request("POST", "customize/git-repositories", desired),
            "creating Git repository",
        )
        if not isinstance(repository, dict):
            raise ArcaneError("creating Git repository returned an unexpected response")
        print(f"Created Arcane Git repository {name!r}")
        return repository, True

    changed_fields = {
        key: value for key, value in desired.items() if existing.get(key) != value
    }
    if not changed_fields:
        print(f"Arcane Git repository {name!r} already matches")
        return existing, False

    # Explicitly remove obsolete credentials whenever the public target or auth
    # mode changes. Arcane also requires this to authorize a credential target
    # URL change safely.
    if any(key in changed_fields for key in {"url", "authType"}):
        changed_fields.update({"username": "", "token": "", "sshKey": ""})

    repository_id = existing.get("id")
    if not isinstance(repository_id, str) or not repository_id:
        raise ArcaneError(f"Git repository {name!r} has no usable ID")
    repository = response_data(
        client.request(
            "PUT", f"customize/git-repositories/{repository_id}", changed_fields
        ),
        "updating Git repository",
    )
    if not isinstance(repository, dict):
        raise ArcaneError("updating Git repository returned an unexpected response")
    print(f"Updated Arcane Git repository {name!r}")
    return repository, True


def _sync_is_healthy(sync: dict[str, Any]) -> bool:
    return (
        sync.get("lastSyncStatus") == "success"
        and isinstance(sync.get("projectId"), str)
        and bool(sync.get("projectId"))
    )


def _verify_sync(client: ArcaneClient, environment_id: str, sync_id: str) -> dict[str, Any]:
    sync = response_data(
        client.request("GET", f"environments/{environment_id}/gitops-syncs/{sync_id}"),
        "reading GitOps sync",
    )
    if not isinstance(sync, dict):
        raise ArcaneError("reading GitOps sync returned an unexpected response")
    if not _sync_is_healthy(sync):
        detail = sync.get("lastSyncError") or sync.get("lastSyncStatus") or "unknown status"
        raise ArcaneError(f"GitOps sync {sync.get('name')!r} is not healthy: {detail}")
    return sync


def ensure_sync(
    client: ArcaneClient,
    *,
    environment_id: str,
    existing_syncs: list[dict[str, Any]],
    name: str,
    compose_path: str,
    repository_id: str,
    branch: str,
    force_sync: bool,
) -> tuple[dict[str, Any], bool]:
    existing = exact_named(existing_syncs, name, "GitOps sync")
    desired = {
        "name": name,
        "repositoryId": repository_id,
        "branch": branch,
        "composePath": compose_path,
        "targetType": "project",
        "projectName": name,
        "autoSync": False,
        "syncInterval": 5,
        "syncDirectory": True,
    }

    created = False
    changed = False
    if existing is None:
        sync = response_data(
            client.request(
                "POST", f"environments/{environment_id}/gitops-syncs", desired
            ),
            "creating GitOps sync",
        )
        if not isinstance(sync, dict):
            raise ArcaneError("creating GitOps sync returned an unexpected response")
        existing_syncs.append(sync)
        existing = sync
        created = True
        changed = True
        print(f"Created Arcane GitOps sync {name!r}")
    else:
        updates = {
            key: value for key, value in desired.items() if existing.get(key) != value
        }
        if updates:
            sync_id = existing.get("id")
            if not isinstance(sync_id, str) or not sync_id:
                raise ArcaneError(f"GitOps sync {name!r} has no usable ID")
            sync = response_data(
                client.request(
                    "PUT",
                    f"environments/{environment_id}/gitops-syncs/{sync_id}",
                    updates,
                ),
                "updating GitOps sync",
            )
            if not isinstance(sync, dict):
                raise ArcaneError("updating GitOps sync returned an unexpected response")
            existing = sync
            changed = True
            print(f"Updated Arcane GitOps sync {name!r}")

    sync_id = existing.get("id")
    if not isinstance(sync_id, str) or not sync_id:
        raise ArcaneError(f"GitOps sync {name!r} has no usable ID")

    # Creation already performs one synchronous initial sync. Re-run only when
    # that attempt failed, or when reconciliation changed an existing source.
    needs_manual_sync = (
        (not created and (changed or force_sync)) or not _sync_is_healthy(existing)
    )
    if needs_manual_sync:
        client.request(
            "POST", f"environments/{environment_id}/gitops-syncs/{sync_id}/sync"
        )
        print(f"Synchronized Arcane GitOps project {name!r}")

    sync = _verify_sync(client, environment_id, sync_id)
    if not changed and not force_sync and not needs_manual_sync:
        print(f"Arcane GitOps sync {name!r} already matches")
    return sync, changed or needs_manual_sync


def retire_sync(
    client: ArcaneClient,
    *,
    environment_id: str,
    existing_syncs: list[dict[str, Any]],
    name: str,
    compose_path: str,
    repository_id: str,
) -> bool:
    existing = exact_named(existing_syncs, name, "GitOps sync")
    if existing is None:
        print(f"Retired Arcane GitOps sync {name!r} is already absent")
        return False

    expected_identity = {
        "repositoryId": repository_id,
        "targetType": "project",
        "projectName": name,
        "composePath": compose_path,
    }
    mismatches = {
        key: (existing.get(key), value)
        for key, value in expected_identity.items()
        if existing.get(key) != value
    }
    if mismatches:
        detail = ", ".join(
            f"{key}={actual!r} (expected {expected!r})"
            for key, (actual, expected) in sorted(mismatches.items())
        )
        raise ArcaneError(
            f"refusing to delete GitOps sync {name!r} with unexpected identity: {detail}"
        )

    sync_id = existing.get("id")
    if not isinstance(sync_id, str) or not sync_id:
        raise ArcaneError(f"GitOps sync {name!r} has no usable ID")
    client.request("DELETE", f"environments/{environment_id}/gitops-syncs/{sync_id}")
    existing_syncs.remove(existing)
    print(f"Deleted retired Arcane GitOps sync {name!r}")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--repository-name", required=True)
    parser.add_argument("--repository-url", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--role-name", required=True)
    parser.add_argument("--credential-name", required=True)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        metavar="NAME=COMPOSE_PATH",
        help="managed project and repository-relative Compose path; repeat per project",
    )
    parser.add_argument(
        "--retired-project",
        action="append",
        default=[],
        metavar="NAME=COMPOSE_PATH",
        help="retired project identity to delete safely; repeat per project",
    )
    parser.add_argument("--environment-id", default="0")
    parser.add_argument("--token-ttl-seconds", type=int, default=3600)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def run(args: argparse.Namespace) -> None:
    api_key = os.environ.get("ARCANE_ADMIN_STATIC_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ARCANE_ADMIN_STATIC_API_KEY is required")
    if not 60 <= args.token_ttl_seconds <= 3600:
        raise ValueError("--token-ttl-seconds must be between 60 and 3600")
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")

    repository_name = args.repository_name.strip()
    role_name = args.role_name.strip()
    credential_name = args.credential_name.strip()
    branch = args.branch.strip()
    environment_id = args.environment_id.strip()
    audience = validate_https_url(args.audience, "--audience")
    subject = args.subject.strip()
    for option, value in (
        ("--repository-name", repository_name),
        ("--branch", branch),
        ("--role-name", role_name),
        ("--credential-name", credential_name),
        ("--environment-id", environment_id),
        ("--subject", subject),
    ):
        if not value:
            raise ValueError(f"{option} must not be empty")

    repository_url = validate_https_url(args.repository_url, "--repository-url")
    issuer = validate_https_url(args.issuer, "--issuer").rstrip("/")
    projects = parse_project_specs(args.project)
    retired_projects = parse_project_specs(
        args.retired_project,
        option="--retired-project",
        require_any=False,
    )
    overlap = sorted(
        {name for name, _ in projects} & {name for name, _ in retired_projects}
    )
    if overlap:
        raise ValueError(
            "projects cannot be both managed and retired: " + ", ".join(overlap)
        )
    client = ArcaneClient(args.base_url, api_key, args.timeout)

    changed = ensure_settings(client, environment_id)
    role, role_changed = ensure_role(client, role_name)
    changed = changed or role_changed
    role_id = role.get("id")
    if not isinstance(role_id, str) or not role_id:
        raise ArcaneError(f"role {role_name!r} has no usable ID")
    _, credential_changed = ensure_credential(
        client,
        name=credential_name,
        issuer=issuer,
        audience=audience,
        subject=subject,
        role_id=role_id,
        environment_id=environment_id,
        token_ttl_seconds=args.token_ttl_seconds,
    )
    changed = changed or credential_changed

    repository, repository_changed = ensure_repository(
        client, repository_name, repository_url
    )
    changed = changed or repository_changed
    repository_id = repository.get("id")
    if not isinstance(repository_id, str) or not repository_id:
        raise ArcaneError(f"Git repository {repository_name!r} has no usable ID")

    syncs = list_data(
        client.request(
            "GET", f"environments/{environment_id}/gitops-syncs?limit=100"
        ),
        "listing GitOps syncs",
    )
    for project_name, compose_path in retired_projects:
        sync_changed = retire_sync(
            client,
            environment_id=environment_id,
            existing_syncs=syncs,
            name=project_name,
            compose_path=compose_path,
            repository_id=repository_id,
        )
        changed = changed or sync_changed

    for project_name, compose_path in projects:
        _, sync_changed = ensure_sync(
            client,
            environment_id=environment_id,
            existing_syncs=syncs,
            name=project_name,
            compose_path=compose_path,
            repository_id=repository_id,
            branch=branch,
            force_sync=repository_changed,
        )
        changed = changed or sync_changed

    print("Arcane reconciliation completed successfully")
    print(f"changed={'true' if changed else 'false'}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        run(args)
    except (ArcaneError, ValueError) as exc:
        print(f"Arcane reconciliation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
