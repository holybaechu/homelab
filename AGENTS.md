# Agent guidance

This is a personal homelab repository for a single-node Talos Kubernetes cluster,
managed with OpenTofu and Flux. Optimize for simple declarative configuration,
readable scripts, and easy maintenance by one person.

## Keep changes small

- Make the smallest change that fulfills the request. Keep unrelated cleanup separate.
- Prefer existing configuration options and direct code. Keep one-off logic local;
  introduce abstractions or dependencies only when the current task needs them.
- Build for the cluster and environments this repository actually supports. Add
  portability, fallback behavior, and configurability only for a concrete requirement.
- Keep tooling lightweight. Add automation or process only when requested or needed
  to solve a recurring problem. Use the tool versions declared in `mise.toml`.
- Follow the existing Kubernetes layers and Flux dependencies. Keep shared cluster
  settings in `kubernetes/clusters/homelab/settings.yaml` and app configuration with
  its workload.

## Infrastructure and data

- Treat Git as the desired state. Keep persistent fixes in the manifests so Flux
  reconciliation preserves them; keep live troubleshooting changes scoped to the task.
- Before removing or renaming resources, check Flux pruning and persistent-volume
  deletion behavior. Include data migration, backup, and recovery steps when affected.
- Before applying Talos node changes, run `scripts/talos-preflight` and verify the
  exact installation disk serial and protected USB identity. Preserve the disk
  confirmation checks and `prevent_destroy` safeguards in `infra/talos/main.tf`.
- Preserve existing encrypted OpenTofu state and its original 1Password passphrase.
  Keep state, plans, generated machine configuration, kubeconfig, and talosconfig
  outside Git. Never replace existing cluster secrets with fresh state to fix an error.

## 1Password and credentials

- Use 1Password as the source of truth for app credentials, API tokens, OIDC client
  secrets, database passwords, and the Talos state passphrase. Reuse existing items
  and fields in the configured vault before creating new ones.
- Keep only item/field references in
  `kubernetes/clusters/homelab/onepassword.yaml`. Follow the existing `*_REF` pattern:
  Flux substitutes the reference into an `ExternalSecret` using
  `secretStoreRef.name: onepassword`; workloads consume the resulting Kubernetes Secret.
- Follow `kubernetes/platform/config/secrets.yaml` for the shared 1Password SDK
  store. Keep its service-account token in the separate `onepassword-token` Secret
  in `external-secrets`; never commit token values or generated Secret contents.
- Verify exact item, section, and field paths. Use item or field IDs when required
  by names or custom fields, following existing references rather than guessing labels.
- Respect the configured secret refresh interval and SDK cache. Avoid unnecessary
  1Password reads or extra stores for individual apps.
- When CLI access is needed, use the existing `op` authentication and inspect only
  what the task needs. Keep secret values out of terminal output, logs, diffs,
  documentation, and commit messages; use references or placeholders in examples.
- For credential changes, check how the app reloads secrets and whether credentials
  only initialize an empty data directory. Keep the app and 1Password in sync during
  rotation; for MySQL, follow `kubernetes/apps/mysql/README.md`.

## Documentation

- Keep READMEs short: purpose, setup entry point, essential cautions, and links.
  Put feature details and implementation notes in the relevant app or infrastructure guide.
- Write instructions people can act on. Use short paragraphs, steps for procedures,
  and tables for shortcuts. Cut repetition and obvious explanations.
- Document each fact once. Update its owning guide and check links when moving
  content. Keep prerequisites and recovery instructions with the affected commands.
- Keep plans, questionnaires, progress reports, research reports, and session notes
  outside the repo. Delete completed plans; retain current usage and maintenance guides.

## Validation

- Default to syntax checks, rendered configuration diffs, and focused manual
  verification. Documentation-only edits need a diff review; use a browser preview
  for changes to app pages, styling, or layout. Run `git diff --check` before committing.
- For Kubernetes changes, follow `.github/workflows/validate.yml`: build the affected
  Kustomize layers, apply strict Flux substitution from both cluster ConfigMaps, and
  validate native resources with kubeconform. Plain YAML parsing is not sufficient.
- For OpenTofu changes, run formatting and configuration validation with isolated
  provider data and the backend disabled, as CI does. For Talos rendering, follow
  the workflow's opt-in temporary-state fixture; keep validation separate from live
  node application and real cluster state.
- Run relevant retained app tests when changing their behavior; use the matching
  workflows under `.github/workflows/` for dependencies and container environments.
  Add a test only for a concrete regression that is difficult to check manually.
  Reuse fixtures and keep machine interactions mocked or isolated. Prefer
  representative behavior checks; coverage targets and exhaustive combinations are unnecessary.

## Commits and pull requests

- Use Conventional Commits for commit messages and PR titles:
  `<type>[optional scope][!]: <description>`.
- Use `feat` for new behavior, `fix` for corrections, and `docs`, `refactor`, `perf`,
  `test`, `build`, `ci`, `style`, `chore`, or `revert` as appropriate.
- Write a short, imperative description beginning with a lowercase word, without
  a trailing period. Add a scope only when it clarifies the affected area.
- Mark breaking changes with `!` and explain the required migration in a
  `BREAKING CHANGE:` footer.
- Name new work branches `<type>/<short-kebab-case-description>`, for example
  `fix/secret-reference`.
- Keep each PR focused on one coherent change. Keep its body proportional to the
  change: describe the result, report validation, and link relevant issues.
