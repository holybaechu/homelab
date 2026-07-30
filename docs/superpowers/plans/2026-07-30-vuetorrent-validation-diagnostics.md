# VueTorrent Validation Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the opaque bundled VueTorrent validation with fail-closed diagnostics that identify asset, qBittorrent preference, and effective Docker-mod failures without exposing container-controlled text or secrets.

**Architecture:** Put the production checks in one POSIX shell script so it can aggregate every internal diagnostic before returning failure and can be exercised directly with a fake Docker executable. Invoke that script from the Docker-host validation play, then leave the existing routed qBittorrent check after the internal checks.

**Tech Stack:** POSIX shell, Docker Compose CLI, Ansible, pytest, Git Bash/Unix `sh`.

## Global Constraints

- Keep the official `DOCKER_MODS` integration unchanged; the image layer and deployed diagnostics establish `/vuetorrent/public` as the managed Web UI root.
- Check `/vuetorrent/public/index.html`, exact `WebUI\AlternativeUIEnabled=true`, exact `WebUI\RootFolder=/vuetorrent/public`, and the effective container `DOCKER_MODS` value separately.
- Emit every internal diagnostic before failing; an early failed check must not stop later checks.
- On failure, inspect at most 80 qBittorrent init-log lines but emit only constant boolean summaries for VueTorrent, Docker-mod, and error/failure mentions; never emit raw container log bytes.
- Print the effective `DOCKER_MODS` value only after it matches the strict official-image-plus-semver allowlist. Suppress invalid, unavailable, and failed-command values with a constant diagnostic.
- Keep the routed `qbt.home.hchu.me` validation after the internal checks.
- Create one scoped commit; do not amend, push, deploy, or alter `.pytest-tmp/`.

---

### Task 1: Executable diagnostic contract

**Files:**

- Create: `infra/ansible/files/validate-vuetorrent.sh`
- Create: `tests/docker/test_vuetorrent_validation.sh`
- Modify: `tests/docker/test_docker_apps_validate_playbook.py`

**Interfaces:**

- Consumes: `docker compose exec -T qbittorrent`, `docker compose logs`, and the media project working directory.
- Produces: four labeled PASS/FAIL diagnostics, exit zero only when all four checks pass, and bounded boolean-only log summaries on failure.

- [ ] **Step 1: Write the failing executable test**

Run the production validator with a fake `docker` at the front of `PATH`. The fake's first check fails, the later checks record their calls, and the log result contains both an ordinary line and a PBKDF2 line. Assert:

```text
FAIL asset: /vuetorrent/public/index.html
FAIL config: exact WebUI\AlternativeUIEnabled=true
FAIL config: exact WebUI\RootFolder=/vuetorrent/public
FAIL environment: effective DOCKER_MODS=<invalid or unavailable; value suppressed>
```

Also assert all four calls occurred after the first failure and `logs --no-color --tail 80 qbittorrent` occurred. Feed the fake logs keyword-free secret material, API-key and Bearer values, ANSI control bytes, and an Actions workflow command; assert none is emitted and only fixed boolean summaries appear. A failed `printenv` or logs command must produce constant output with no captured stderr. A success fixture must exit zero, emit four PASS lines, and not request logs.

- [ ] **Step 2: Run the test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\docker\test_docker_apps_validate_playbook.py -q
```

Expected: FAIL because `infra/ansible/files/validate-vuetorrent.sh` and its executable diagnostics do not exist.

- [ ] **Step 3: Implement the minimal aggregator**

Use a failure counter rather than `set -e`. Each Docker command runs in its own `if`, emits a labeled result, and increments the counter on failure. Validate the effective mod with this Renovate-compatible shape:

```sh
^ghcr\.io/vuetorrent/vuetorrent-lsio-mod:[0-9]+\.[0-9]+\.[0-9]+$
```

If the counter is nonzero, request exactly `docker compose logs --no-color --tail 80 qbittorrent`, derive fixed yes/no summaries without emitting any raw line, and exit 1. Only echo `DOCKER_MODS` after the strict allowlist matches; otherwise print `<invalid or unavailable; value suppressed>`.

- [ ] **Step 4: Run the focused test to verify GREEN**

Run the same focused pytest command and require all tests to pass.

### Task 2: Ansible wiring and recovery verification

**Files:**

- Modify: `infra/ansible/playbooks/validate.yml`
- Modify: `tests/docker/test_docker_apps_validate_playbook.py`
- Modify: `C:\tmp\homelab-cd-recovery-fix-report.md` outside the commit.

**Interfaces:**

- Consumes: the Task 1 validator and `{{ docker_apps_compose_root }}/media`.
- Produces: Ansible failure output with internal diagnostics before the existing DNS and routed checks.

- [ ] **Step 1: Add the structural RED assertion**

Require the playbook to invoke `validate-vuetorrent.sh`, require that invocation to precede the `Check private Traefik routes` task, and reject the old bundled `set -eu` shell block.

- [ ] **Step 2: Run focused pytest and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\docker\test_docker_apps_validate_playbook.py -q
```

Expected: FAIL until the playbook invokes the new validator.

- [ ] **Step 3: Replace the bundled task**

Invoke the local script through `ansible.builtin.script` with the remote working directory set to `{{ docker_apps_compose_root }}/media`; retain `changed_when: false`. Do not alter the later route loop containing `qbt.home.hchu.me`.

- [ ] **Step 4: Verify and commit once**

Run focused pytest, full pytest, POSIX shell syntax for both scripts, Ansible syntax for `validate.yml`, YAML parsing, and `git diff --check`. Stage only the plan, validator, tests, and playbook, then create one new commit. Append Round 5 evidence to the recovery report after the commit; do not push or deploy.

Execution choice was already supplied by the recovery request: execute inline in this task with one final review gate.
