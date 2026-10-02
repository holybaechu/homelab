# Homelab agent guide

This is a personal homelab repository and the public desired state for two
production units: `tailnet` and `docker_apps`. Optimize for small,
readable changes and straightforward maintenance by one person.

## Keep changes small

- Begin with `git status --short` and the diff around the target. Preserve
  unrelated working-tree artifacts and compare validation failures with the
  pre-change baseline.
- Make the smallest change at the layer that owns the behavior. Keep unrelated
  cleanup separate; prefer existing configuration options and direct code.
- Keep one-off logic local. Add abstractions, dependencies, portability, or
  fallback behavior only for a concrete requirement.
- Reuse the existing reconciliation and release entrypoints. Add tooling or
  automation only when requested or needed to solve a recurring problem.

## Context map

Read the guides for each area touched by the change:

- **Host topology or primitives:** read `infra/README.md`, then the selected
  role and `infra/ansible/playbooks/reconcile.yml`. Reconciliation targets one
  of `pve`, `tailnet`, `apps-host`; select exactly one unit
  per invocation.
- **Application service:** read `apps/compose/homelab/README.md` and
  `docs/runbooks/compose-release.md`. The complete application release package
  lives under `apps/compose/homelab`; keep service configuration, preparation,
  smoke behavior, and release inputs co-located there.
- **Deployment workflow:** read `docs/runbooks/github-actions.md` and
  `docs/runbooks/compose-release.md` before editing `.github/workflows/**` or
  `scripts/ci/**`. The apps lane uses one release engine and SSH wrapper; preserve its
  transaction model.
- **Secret schema:** read `secrets/README.md` and the component's adjacent
  validator. Keep schemas beside consumers and commit placeholders or
  structure only.
- **Storage or disaster recovery:** read
  `docs/runbooks/homelab-storage-resize.md` or `docs/runbooks/recovery.md`.
  Repository declarations and live destructive storage work are separate
  stages.

## Repository invariants

- `infra/ansible/inventory/prod/topology.json` is the sole topology source for
  hosts, addresses, VMIDs, resources, mounts, and unit selection. Derive these
  values from it rather than adding independent overrides or copies.
- Keep application deployment out of Ansible; Ansible owns host primitives,
  while immutable Compose packages own runtime releases.
- Pin container images by readable tag and exact digest. Pin GitHub Actions by
  full commit SHA and retain the readable version comment used by Renovate.
- Preserve exact-commit descriptors, digest-bound images, inactive-slot
  activation, semantic smoke checks, and automatic rollback in release paths.
- Keep secret values and secret hashes out of descriptors, state, logs, and
  tracked files. Production receives one versioned JSON bundle per component.
- Preserve durable mounts and volumes. Follow the owning runbook for live
  storage changes, backups, and recovery.

## Documentation

- Keep READMEs short: purpose, setup entrypoint, essential cautions, and links.
  Put operating procedures and recovery steps in the relevant runbook.
- Document each fact once in its owning guide. Update links when moving content
  and update the operator runbook when the operating contract changes.
- Write actionable instructions: short paragraphs, steps for procedures, and
  tables for comparisons. Keep prerequisites and recovery instructions beside
  the affected commands.
- Keep plans, questionnaires, progress reports, and session notes outside the
  repository. Retain current usage and maintenance guides.

## Validation

Co-locate each behavior change with the nearest regression test under the
matching `tests/` branch. Prefer representative behavior checks and reuse
existing fixtures; isolate production interactions. Documentation-only changes
need a diff review and a check of referenced paths, without a new behavior test.

Run the narrowest affected test while iterating, then run the same repository
gates as CI from the root. Use a shell with Bash, Python and the dependencies in
`requirements-dev.txt` and `requirements-deploy.txt`, Docker Compose, and Ansible
available:

```sh
python -m pytest -q
./scripts/ci/validate-compose.sh
export ANSIBLE_CONFIG=infra/ansible/ansible.cfg
for unit in pve tailnet apps-host; do
  ansible-playbook \
    -i infra/ansible/inventory/prod/topology.json \
    infra/ansible/playbooks/reconcile.yml \
    --syntax-check \
    -e "homelab_unit=$unit" \
    -e "homelab_secret_bundle=/tmp/not-read-during-syntax-check.json"
done
```

Before completion, inspect `git diff --check` and `git diff --stat`, and review
new untracked files separately because those commands omit them. Report each
gate's exit status, or the exact missing prerequisite when it could not run.
Completion means the affected behavior has a passing regression test, all
available gates pass or an environmental/baseline failure is identified
precisely, documentation matches behavior, and unrelated working-tree state
remains intact.

## Commits and pull requests

- Use Conventional Commits: `<type>[optional scope][!]: <description>`. Choose
  `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `build`, `ci`, `style`,
  `chore`, or `revert` to match the change.
- Write a short, imperative description beginning with a lowercase word,
  without a trailing period. Add a scope only when it clarifies the affected
  area. Example: `fix(apps): correct adguard release mount`.
- Mark breaking changes with `!` and explain the required migration in a
  `BREAKING CHANGE:` footer.
- Use `codex/<short-kebab-case-description>` for new agent work branches unless
  the user requests a different name.
- Keep each PR focused on one coherent change. Its title follows the commit
  format above; its body explains the problem, resulting behavior, validation
  results, and any operator action.
