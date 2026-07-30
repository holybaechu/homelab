# VueTorrent Validation Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the opaque bundled VueTorrent validation with fail-closed diagnostics that identify asset, qBittorrent preference, and effective Docker-mod failures without exposing secrets.

**Architecture:** Put the production checks in one POSIX shell script so it can aggregate every internal diagnostic before returning failure and can be exercised directly with a fake Docker executable. Invoke that script from the Docker-host validation play, then leave the existing routed qBittorrent check after the internal checks.

**Tech Stack:** POSIX shell, Docker Compose CLI, Ansible, pytest, Git Bash/Unix `sh`.

## Global Constraints

- Do not change or guess the VueTorrent integration itself.
- Check `/vuetorrent/index.html`, exact `WebUI\AlternativeUIEnabled=true`, exact `WebUI\RootFolder=/vuetorrent`, and the effective container `DOCKER_MODS` value separately.
- Emit every internal diagnostic before failing; an early failed check must not stop later checks.
- On failure, include at most 80 qBittorrent init-log lines, at most 400 characters per line, and redact lines that may contain credentials, tokens, private keys, PBKDF2 values, or hashes.
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
- Produces: four labeled PASS/FAIL diagnostics, exit zero only when all four checks pass, and a sanitized bounded log tail only on failure.

- [ ] **Step 1: Write the failing executable test**

Run the production validator with a fake `docker` at the front of `PATH`. The fake's first check fails, the later checks record their calls, and the log result contains both an ordinary line and a PBKDF2 line. Assert:

```text
FAIL asset: /vuetorrent/index.html
FAIL config: exact WebUI\AlternativeUIEnabled=true
FAIL config: exact WebUI\RootFolder=/vuetorrent
FAIL environment: effective DOCKER_MODS=unexpected-mod
```

Also assert all four calls occurred after the first failure, `logs --no-color --tail 80 qbittorrent` occurred, the ordinary line is visible, and the PBKDF2 value is absent. A success fixture must exit zero, emit four PASS lines, and not request logs.

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

If the counter is nonzero, request exactly `docker compose logs --no-color --tail 80 qbittorrent`, redact sensitive-looking lines with `awk`, truncate every emitted line to 400 characters, and exit 1.

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
