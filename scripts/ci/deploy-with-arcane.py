#!/usr/bin/env python3
"""Deploy selected homelab Compose projects through Arcane GitOps."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, unquote_plus, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
JWT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
COMMIT_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


class ArcaneError(RuntimeError):
    """Raised when authentication or an Arcane operation fails."""


class ArcaneTransientError(ArcaneError):
    """Raised for a transport failure that may clear after a proxy restart."""


def api_root(base_url: str) -> str:
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


def validate_https_url(value: str, option: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{option} must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError(f"{option} must not contain credentials or a fragment")
    return candidate


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


def _read_http_error(exc: HTTPError) -> str:
    raw = exc.read(16_384)
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    return _response_error(payload)


def github_oidc_request_url(request_url: str, audience: str) -> str:
    parsed = urlsplit(request_url.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("ACTIONS_ID_TOKEN_REQUEST_URL must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("ACTIONS_ID_TOKEN_REQUEST_URL is not safe to use")
    # Preserve GitHub's opaque query bytes exactly; only replace the audience
    # item. Re-encoding unrelated signed/opaque values can change semantics.
    query = [
        item
        for item in parsed.query.split("&")
        if item and unquote_plus(item.split("=", 1)[0]) != "audience"
    ]
    query.append(f"audience={quote_plus(audience)}")
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "&".join(query), parsed.fragment)
    )


def fetch_github_oidc_token(audience: str, timeout: float) -> str:
    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "").strip()
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "").strip()
    if not request_url or not request_token:
        raise ArcaneError(
            "GitHub OIDC variables are unavailable; the workflow needs id-token: write"
        )

    request = Request(
        github_oidc_request_url(request_url, audience),
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {request_token}",
            "User-Agent": "homelab-arcane-deployer/1",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
        payload = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        detail = _read_http_error(exc)
        raise ArcaneError(
            f"GitHub OIDC request returned HTTP {exc.code}: {detail}"
        ) from None
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ArcaneError(f"GitHub OIDC request failed: {reason}") from None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArcaneError(f"GitHub OIDC returned invalid JSON: {exc}") from None

    token = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise ArcaneError("GitHub OIDC response did not contain a token")
    return token


def exchange_federated_token(
    base_url: str, audience: str, subject_token: str, timeout: float
) -> str:
    form = urlencode(
        {
            "grant_type": TOKEN_EXCHANGE_GRANT,
            "subject_token": subject_token,
            "subject_token_type": JWT_TOKEN_TYPE,
            "audience": audience,
            "requested_token_type": ACCESS_TOKEN_TYPE,
        }
    ).encode("utf-8")
    request = Request(
        f"{api_root(base_url)}/auth/federated/token",
        data=form,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "homelab-arcane-deployer/1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
        payload = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        detail = _read_http_error(exc)
        raise ArcaneError(
            f"Arcane federated token exchange returned HTTP {exc.code}: {detail}"
        ) from None
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ArcaneError(f"Arcane federated token exchange failed: {reason}") from None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArcaneError(f"Arcane token exchange returned invalid JSON: {exc}") from None

    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise ArcaneError("Arcane token exchange response did not contain an access token")
    return token


class ArcaneClient:
    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        request_timeout: float,
    ) -> None:
        self.root = api_root(base_url)
        self.bearer_token = bearer_token
        self.request_timeout = request_timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.bearer_token}",
            "User-Agent": "homelab-arcane-deployer/1",
        }

    def request(
        self,
        method: str,
        path: str,
        payload: Any | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        body = None
        headers = self._headers()
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.root}/{path.lstrip('/')}",
            data=body,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urlopen(
                request,
                timeout=self.request_timeout if timeout is None else timeout,
            ) as response:
                raw = response.read()
            result = json.loads(raw.decode("utf-8")) if raw else None
        except HTTPError as exc:
            detail = _read_http_error(exc)
            error_type = (
                ArcaneTransientError if exc.code in {502, 503, 504} else ArcaneError
            )
            raise error_type(
                f"{method.upper()} {path} returned HTTP {exc.code}: {detail}"
            ) from None
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ArcaneTransientError(
                f"{method.upper()} {path} could not reach Arcane: {reason}"
            ) from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArcaneError(
                f"{method.upper()} {path} returned invalid JSON: {exc}"
            ) from None

        if isinstance(result, dict) and result.get("success") is False:
            raise ArcaneError(
                f"{method.upper()} {path} failed: {_response_error(result)}"
            )
        return result

def response_data(response: Any, operation: str) -> Any:
    if not isinstance(response, dict) or "data" not in response:
        raise ArcaneError(f"{operation} returned an unexpected response")
    return response["data"]


def list_data(response: Any, operation: str) -> list[dict[str, Any]]:
    data = response_data(response, operation)
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ArcaneError(f"{operation} returned an unexpected item list")
    return data


def parse_names(value: str, option: str) -> list[str]:
    if not value.strip():
        return []
    names: list[str] = []
    seen: set[str] = set()
    for part in value.split(","):
        name = part.strip()
        if not name or not NAME_PATTERN.fullmatch(name):
            raise ValueError(f"{option} contains invalid project name {name!r}")
        if name in seen:
            raise ValueError(f"{option} contains duplicate project name {name!r}")
        seen.add(name)
        names.append(name)
    return names


def exact_sync(
    syncs: list[dict[str, Any]], project_name: str
) -> dict[str, Any]:
    matches = [sync for sync in syncs if sync.get("name") == project_name]
    if not matches:
        raise ArcaneError(f"Arcane has no GitOps sync named {project_name!r}")
    if len(matches) > 1:
        raise ArcaneError(f"Arcane has multiple GitOps syncs named {project_name!r}")
    sync = matches[0]
    if sync.get("projectName") != project_name:
        raise ArcaneError(
            f"GitOps sync {project_name!r} targets unexpected project "
            f"{sync.get('projectName')!r}"
        )
    return sync


def assert_expected_commit(
    sync: dict[str, Any], expected_commit: str, project_name: str
) -> None:
    status = sync.get("lastSyncStatus")
    if status != "success":
        detail = sync.get("lastSyncError") or status or "unknown status"
        raise ArcaneError(f"GitOps sync {project_name!r} is not healthy: {detail}")
    actual = sync.get("lastSyncCommit")
    if not isinstance(actual, str) or actual.strip().lower() != expected_commit.lower():
        shown = actual.strip() if isinstance(actual, str) and actual.strip() else "missing"
        raise ArcaneError(
            f"GitOps sync {project_name!r} deployed commit {shown}, "
            f"expected {expected_commit}"
        )


def deployment_order(projects: list[str]) -> list[str]:
    """Keep the Traefik-hosting platform deployment last."""

    return [name for name in projects if name != "platform"] + [
        name for name in projects if name == "platform"
    ]


def poll_synced_commit(
    client: ArcaneClient,
    *,
    environment_id: str,
    sync_id: str,
    project_name: str,
    previous_sync_at: Any,
    expected_commit: str,
    timeout: float,
    interval: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_observation = "no fresh sync status"
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ArcaneError(
                f"timed out waiting for GitOps sync {project_name!r} at "
                f"{expected_commit}: {last_observation}"
            )

        try:
            response = client.request(
                "GET",
                f"environments/{environment_id}/gitops-syncs/{sync_id}",
                timeout=min(client.request_timeout, max(1.0, remaining)),
            )
            fresh = response_data(response, f"reading GitOps sync {project_name!r}")
            if not isinstance(fresh, dict):
                raise ArcaneError(
                    f"reading GitOps sync {project_name!r} returned bad data"
                )

            sync_at = fresh.get("lastSyncAt")
            status = fresh.get("lastSyncStatus")
            commit = fresh.get("lastSyncCommit")
            shown_commit = commit if isinstance(commit, str) and commit else "missing"
            last_observation = (
                f"status={status or 'missing'}, commit={shown_commit}, "
                f"lastSyncAt={sync_at or 'missing'}"
            )

            # A previous successful row is not proof that this trigger ran. A
            # changed lastSyncAt is Arcane's durable completion marker for both
            # successful and failed attempts.
            if sync_at != previous_sync_at and sync_at is not None:
                if status == "failed":
                    detail = fresh.get("lastSyncError") or "unknown sync failure"
                    raise ArcaneError(
                        f"GitOps sync {project_name!r} failed: {detail}"
                    )
                if status == "success":
                    assert_expected_commit(fresh, expected_commit, project_name)
                    return fresh
        except ArcaneTransientError as exc:
            last_observation = str(exc)

        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))


def deploy_project(
    client: ArcaneClient,
    *,
    environment_id: str,
    sync: dict[str, Any],
    project_name: str,
    expected_commit: str,
    sync_timeout: float,
    poll_interval: float,
) -> None:
    sync_id = sync.get("id")
    if not isinstance(sync_id, str) or not sync_id:
        raise ArcaneError(f"GitOps sync {project_name!r} has no usable ID")

    previous_sync_at = sync.get("lastSyncAt")
    print(f"Synchronizing Arcane project {project_name!r}")
    try:
        client.request(
            "POST",
            f"environments/{environment_id}/gitops-syncs/{sync_id}/sync",
            timeout=client.request_timeout,
        )
    except ArcaneTransientError:
        # The sync endpoint is synchronous. Deploying platform can restart the
        # Traefik path before the response reaches CI even though Arcane keeps
        # working. Never issue a blind second trigger; observe its durable row.
        print(
            f"Arcane connection changed during {project_name!r} sync; "
            "waiting for its recorded result"
        )

    fresh = poll_synced_commit(
        client,
        environment_id=environment_id,
        sync_id=sync_id,
        project_name=project_name,
        previous_sync_at=previous_sync_at,
        expected_commit=expected_commit,
        timeout=sync_timeout,
        interval=poll_interval,
    )
    print(f"Verified {project_name!r} at commit {expected_commit}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--projects", required=True, help="comma-separated sync names")
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--environment-id", default="0")
    parser.add_argument("--request-timeout", type=float, default=60.0)
    parser.add_argument("--sync-timeout", type=float, default=600.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=1800.0)
    return parser


def run(args: argparse.Namespace) -> None:
    # Validate even a no-op invocation so configuration errors do not pass CI.
    api_root(args.base_url)
    audience = validate_https_url(args.audience, "--audience")
    environment_id = args.environment_id.strip()
    if not environment_id:
        raise ValueError("--environment-id must not be empty")
    for option, value in (
        ("--request-timeout", args.request_timeout),
        ("--sync-timeout", args.sync_timeout),
        ("--poll-interval", args.poll_interval),
        ("--timeout", args.timeout),
    ):
        if value <= 0:
            raise ValueError(f"{option} must be positive")

    projects = parse_names(args.projects, "--projects")

    expected_commit = args.expected_commit.strip().lower()
    if not COMMIT_PATTERN.fullmatch(expected_commit):
        raise ValueError("--expected-commit must be a full 40- or 64-digit Git hash")
    if not projects:
        print("No Arcane projects selected; nothing to deploy")
        return

    request_timeout = min(args.request_timeout, 60.0)
    subject_token = fetch_github_oidc_token(audience, request_timeout)
    bearer_token = exchange_federated_token(
        args.base_url, audience, subject_token, request_timeout
    )
    # Deliberately never print either token.
    client = ArcaneClient(args.base_url, bearer_token, request_timeout)
    syncs = list_data(
        client.request(
            "GET", f"environments/{environment_id}/gitops-syncs?limit=100"
        ),
        "listing GitOps syncs",
    )

    for project_name in deployment_order(projects):
        deploy_project(
            client,
            environment_id=environment_id,
            sync=exact_sync(syncs, project_name),
            project_name=project_name,
            expected_commit=expected_commit,
            sync_timeout=args.sync_timeout,
            poll_interval=args.poll_interval,
        )

    print("Arcane deployment completed successfully")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        run(args)
    except (ArcaneError, ValueError) as exc:
        print(f"Arcane deployment failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
