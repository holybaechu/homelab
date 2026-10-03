# Agent guidance

This is a personal homelab repository for two production LXCs: `tailnet` and
`docker_apps`. Optimize for simple configuration, readable scripts, and easy
maintenance by one person.

## Keep changes small

- Begin with `git status --short` and the diff around the target. Preserve unrelated working-tree changes and untracked files.
- Make the smallest change that fulfills the request at the layer that owns the behavior. Keep unrelated cleanup separate.
- Prefer existing configuration options and direct code. Keep one-off logic local; introduce abstractions or dependencies only when the current task needs them.
- Build for the environments this repository actually supports. Add portability, fallback behavior, and configurability only for a concrete requirement.
- Keep tooling lightweight. Reuse the existing reconciliation and release entrypoints; add automation or process only when requested or needed to solve a recurring problem.

## Homelab boundaries

- Derive hosts, addresses, VMIDs, resources, mounts, and unit selection from `infra/ansible/inventory/prod/topology.json`, the sole topology source.
- Keep host provisioning and primitives in Ansible, and application configuration and activation in the immutable Compose package. Reconcile exactly one unit per invocation: `pve`, `tailnet`, or `apps-host`.
- Keep service configuration, preparation, smoke behavior, and release inputs together under `apps/compose/homelab`.
- Pin container images by readable tag and exact digest. Pin GitHub Actions by full commit SHA with the readable version comment used by Renovate.
- Preserve the shared release engine and SSH wrapper, exact-commit descriptors, digest-bound images, inactive-slot activation, semantic smoke checks, and automatic rollback.
- Keep secrets in private versioned component JSON bundles. Commit schema or placeholders only; keep secret values and hashes out of tracked files, descriptors, state, and logs.
- Preserve durable mounts and volumes. Treat repository declarations and live destructive storage work as separate stages, following the owning runbook for live operations.

## Read the owning guide

Read the relevant guides before changing these areas:

| Area | Starting points |
| --- | --- |
| Host topology or primitives | [Infrastructure](infra/README.md), then the selected role and [reconciliation playbook](infra/ansible/playbooks/reconcile.yml) |
| Application services | [Application package](apps/compose/homelab/README.md) and [release operations](docs/operations.md) |
| `.github/workflows/**` or `scripts/ci/**` | [Workflow contracts](docs/operations.md#run-a-workflow) and [release operations](docs/operations.md) |
| Secret schemas | [Secrets](secrets/README.md) and the component's owning validator |
| Storage or disaster recovery | [Storage resize](docs/recovery.md#storage-maintenance) or [recovery](docs/recovery.md) |

## Documentation

- Keep READMEs short: purpose, setup entry point, essential cautions, and links. Put operating procedures and recovery steps in the relevant runbook.
- Write instructions people can act on. Use short paragraphs, steps for procedures, and tables for comparisons. Cut repetition, obvious explanations, and descriptions of unchanged behavior.
- Document each fact once. Update its owning guide when the operating contract changes and check links when moving content. Keep prerequisites and recovery instructions with the affected commands.
- Keep plans, questionnaires, progress reports, research reports, and session notes outside the repo. Retain current usage and maintenance guides.

## Validation

- Documentation-only edits need a diff review and a check of referenced paths; a new behavior test is unnecessary.
- Co-locate behavior changes with regression tests under the matching `tests/` branch. Focus on costly failures: release activation and recovery, destructive PVE changes, credential handling, control-path availability, and durable-data isolation.
- Prefer representative behavior checks over exhaustive combinations or assertions about implementation details. Reuse existing fixtures and keep production interactions mocked or isolated.
- Run the narrowest affected tests while iterating, then the [local validation gates](README.md#validate-locally) used by [.github/workflows/validate.yml](.github/workflows/validate.yml): pytest, Compose rendering, and Ansible syntax checks for all three explicit units. Use the documented Linux or WSL prerequisites.
- Compare failures with the pre-change baseline. Report each gate's exit status, or the exact missing prerequisite when it could not run.
- Before completion, inspect `git diff --check` and `git diff --stat`, review new untracked files separately, and confirm unrelated working-tree state remains intact.

Complete a behavior change when its regression test passes, available gates pass or an environmental or baseline failure is identified precisely, and documentation matches the resulting behavior.

## Commit messages and PR titles

Use Conventional Commits: `<type>[optional scope][!]: <description>`.

- Use `feat` for new behavior, `fix` for corrections, and `docs`, `refactor`, `perf`, `test`, `build`, `ci`, `style`, `chore`, or `revert` as appropriate.
- Write a short, imperative description beginning with a lowercase word, without a trailing period. Add a scope only when it clarifies the affected area.
- Mark breaking changes with `!` and explain the required migration in a `BREAKING CHANGE:` footer.
- Example: `fix(apps): correct adguard release mount`.

## Branches and PR bodies

- Use `codex/<short-kebab-case-description>` for new agent work branches unless the user requests a different name.
- Keep each PR focused on one coherent change. Its title follows the commit format above.
- Explain the problem, resulting behavior, validation results, and any operator action in the PR body. Keep small-change descriptions short.
