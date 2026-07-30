# Minecraft Retirement, Renovate Coverage, and VueTorrent Repair Design

> Recovery correction (2026-07-30): the bind-mounted Minecraft data is owned
> by Proxmox, not the unprivileged Docker LXC. The exact host path
> `/var/lib/homelab/minecraft` is deleted by the `pve_retire_minecraft_data`
> role in `site.yml` only after Docker proves no
> `com.docker.compose.project=game` containers remain. The VMID/archive
> tombstone remains separate in `prepare-low-id-cutover.yml`.

## Goal

Permanently retire every Minecraft runtime and repository artifact, restore VueTorrent as qBittorrent's managed alternative Web UI, and ensure every repository-controlled dependency version is discoverable and updated by Renovate while operating-system packages are upgraded deterministically by Ansible.

## Decisions

- Minecraft data is intentionally unrecoverable after deployment. The cleanup includes the Compose project, Proxmox-host path `/var/lib/homelab/minecraft`, legacy VMID 115, and local `vzdump-lxc-115-*` archives.
- Renovate keeps the current risk policy: minor and patch updates automerge, while major updates require review.
- VueTorrent uses the official LinuxServer Docker mod rather than a separate web server or a custom qBittorrent image.
- All source-controlled versions must be either handled by a built-in Renovate manager or carry an explicit custom-manager annotation/configuration.
- Debian, Docker Engine, Compose, Tailscale, and other packages installed from apt repositories are runtime dependencies, not Renovate dependencies when their versions are not stored in Git. Ansible upgrades those packages using `state: latest` with a refreshed package index.
- The pre-existing untracked `.pytest-tmp/` directory is unrelated and must remain untouched.

## Minecraft Retirement

### Repository removal

Remove the entire `apps/compose/game` project, `game.env.j2`, `velocity.toml.j2`, Minecraft inventory variables, Minecraft validation tasks, Minecraft-specific tests, and the Minecraft runbook. Update shared application documentation so the active Compose topology contains only `platform`, `media`, and `hermes`.

Remove Minecraft directory creation and permission reconciliation from both the Docker application role and Proxmox homelab-storage role. Remove migration and cutover documentation that presents Minecraft as an active service.

### Deployed cleanup

Declare the former `game` Compose project as retired so the Docker application role runs:

```text
docker compose down --volumes --remove-orphans
```

from `/opt/homelab-compose/game`, then removes that deployed project directory. The cleanup must tolerate an already-absent project.

After the Compose project is stopped, always query Docker for every container
labeled `com.docker.compose.project=game`; an absent `compose.yml` must not
skip this postcondition. Only after the query is empty may the Proxmox-host
role delete `/var/lib/homelab/minecraft`. That role independently repeats the
Docker query through delegation to `docker_apps`, verifies that
`/var/lib/homelab` is the root of the expected `/dev/pve/homelab-data` ext4
filesystem by canonical block-device identity, rejects bind aliases and every
mountpoint at or below the target, and keeps the deletion on one filesystem.
Empty, root, traversal, glob, LXC-view, symlink, noncanonical, and alternate
device paths must fail closed. This data role runs in `site.yml` after the
Docker Compose play, never in `prepare-low-id-cutover.yml`.

On Proxmox, add an idempotent Minecraft retirement task that:

1. Checks whether VMID 115 exists.
2. Refuses to act unless its hostname is exactly `minecraft`.
3. Stops the LXC if it is running.
4. Destroys VMID 115 with purge and unreferenced-disk removal.
5. Deletes only files directly under `/var/lib/vz/dump` whose basename matches `vzdump-lxc-115-*.tar.zst`.
6. Reports no change when neither the LXC nor matching archives exist.

No wildcard is passed directly to a destructive command. Matching archive paths are enumerated and validated inside `/var/lib/vz/dump` before deletion.

## VueTorrent Repair

### Root cause

The earlier native qBittorrent role installed VueTorrent and rendered:

```ini
WebUI\AlternativeUIEnabled=true
WebUI\RootFolder={{ qbittorrent_vuetorrent_root_current }}
```

The two-LXC Compose migration removed both settings and did not add any VueTorrent image, mod, or bind mount. The current deployment therefore cannot serve VueTorrent.

### Runtime integration

Add the official LinuxServer mod to the qBittorrent service:

```yaml
DOCKER_MODS: ghcr.io/vuetorrent/vuetorrent-lsio-mod:2.34.0
```

The version is an initial pinned value; Renovate owns subsequent changes. Keep the existing LinuxServer qBittorrent image and Gluetun network namespace.

Restore these managed qBittorrent preferences:

```ini
WebUI\AlternativeUIEnabled=true
WebUI\RootFolder=/vuetorrent
```

The existing candidate/compare/install sequence remains responsible for stopping qBittorrent before changing its configuration. Compose recreation then loads the mod and restarts qBittorrent with the managed settings.

### Validation

Production validation must prove all three layers:

- `/vuetorrent/index.html` exists inside the qBittorrent container.
- The installed qBittorrent configuration contains `WebUI\AlternativeUIEnabled=true` and `WebUI\RootFolder=/vuetorrent`.
- `https://qbt.home.hchu.me/` responds successfully after recreation.

This distinguishes asset installation, qBittorrent selection, and reverse-proxy reachability.

## Renovate Coverage

### Built-in managers

Keep `config:recommended` and the existing Compose, Dockerfile, GitHub Actions, pip requirements, Ansible Galaxy, and Terraform manager coverage. Keep minor/patch platform automerge only when the current version does not start with `0`; major updates and `0.x` updates remain review-gated.

Every GitHub Action pinned by SHA must include its originating version comment, for example:

```yaml
uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7
```

Renovate uses that comment to associate the immutable SHA with a release line and update it.

### Custom managers

Add focused managers rather than one broad cross-file expression:

1. **OpenTofu:** match the complete semantic version in `.opentofu-version` and use `github-releases` for `opentofu/opentofu`, stripping the leading `v` from releases.
2. **Tailscale action input:** match the annotated `version` under the Tailscale GitHub Action and use `github-releases` for `tailscale/tailscale`.
3. **VueTorrent mod:** match the `DOCKER_MODS` image tag in the media Compose file and use the Docker datasource for `ghcr.io/vuetorrent/vuetorrent-lsio-mod`.
4. **Proxmox Debian template:** use an HTML custom datasource at `https://download.proxmox.com/images/system/`, extract filenames with `^debian-13-standard_(?<version>.+)_amd64\.tar\.zst$`, and update both identical template IDs in `containers.auto.tfvars` in one branch.
5. **1Password CLI:** introduce one upstream-semver-only `OP_CLI_VERSION` build argument in the Hermes Dockerfile, install the APT package at its Debian revision as `1password-cli=${OP_CLI_VERSION}-1`, and source available upstream versions from the official 1Password CLI release-history endpoint `https://app-updates.agilebits.com/product_history/CLI2` through an HTML custom datasource.

The managers must use file-specific patterns and exact named captures so unrelated numeric values cannot be changed.

### Dependency normalization

- Change direct Python requirements from open-ended minimum constraints to exact versions so Renovate PRs and CI environments are deterministic.
- Change the `community.general` Ansible collection from an open-ended minimum to an exact version.
- Replace the duplicated versioned local image name `homelab/hermes-agent:2026.7.7.2` with a constant local build tag such as `homelab/hermes-agent:local`. The external Hermes version remains solely in the Dockerfile `FROM` line, where Renovate already updates its tag and digest.
- Add `.opentofu-version` to the CD path filter so a Renovate OpenTofu update reaches deployment.
- Remove obsolete inline Renovate comments in scripts that do not contain the version they claim to manage.

### Coverage regression test

Extend repository tests to assert:

- action SHA pins have version comments;
- `.opentofu-version`, the Tailscale action input, VueTorrent, the Proxmox template, and 1Password CLI each have a matching manager;
- direct Python and Ansible collection dependencies are exact;
- external Compose and Dockerfile images do not use `latest` aliases;
- the Hermes local build tag carries no duplicated upstream version;
- deleted Minecraft artifacts and active-project entries cannot return unnoticed.

The test is a repository policy check, not a replacement for validating Renovate's configuration with Renovate's official config validator.

## Operating-System Package Updates

Use `state: latest` for repository-installed Debian packages, Docker Engine and its plugins, and Tailscale. Refresh apt metadata before resolving upgrades. Service handlers restart a service only when an upgraded package requires it; Compose projects continue to be recreated only by their existing deployment reconciliation.

This deliberately separates two mechanisms:

- Renovate produces reviewable Git changes for versions stored in the repository.
- Ansible upgrades packages whose versions are owned by configured apt repositories and are not stored in Git.

Documentation and tests must not describe apt runtime upgrades as Renovate PR coverage.

## Failure Handling and Safety

- Minecraft cleanup stops containers before deleting data.
- Missing Compose files do not bypass the Docker label postcondition, and the
  Proxmox data role independently rechecks it on `docker_apps`.
- The storage guard proves the canonical homelab-data block device and rejects
  exact-path or descendant bind mounts before deleting on one filesystem.
- Every destructive target is an exact path or a path validated beneath an exact parent.
- VMID 115 is destroyed only after an exact hostname match.
- Failure to validate a destructive target aborts deployment before deletion.
- VueTorrent deployment fails validation if assets, qBittorrent settings, or the routed page are missing.
- Renovate configuration validation is required before accepting the dependency-coverage changes.
- Minecraft cleanup is intentionally irreversible; there is no rollback path for its deleted data or local archives.

## Verification

Implementation is complete only after all of the following pass:

1. Focused failing tests demonstrate the old Minecraft topology, absent VueTorrent integration, and Renovate gaps.
2. Focused tests pass after the implementation.
3. The complete pytest suite passes.
4. Every remaining Compose manifest passes `docker compose config --quiet` or the repository's structural fallback when Docker is unavailable.
5. The Renovate configuration passes the official config validator.
6. Ansible `bootstrap.yml`, `prepare-low-id-cutover.yml`, `site.yml`, and `validate.yml` pass syntax checks in the project dependency environment.
7. If deployment is authorized, CI and CD pass and production validation proves Minecraft is absent and VueTorrent is active.

## Out of Scope

- Adding Watchtower or another unattended container updater.
- Automatically merging major dependency updates.
- Preserving Minecraft worlds, plugins, VMID 115, or matching local Proxmox archives.
- Replacing qBittorrent, Gluetun, Traefik, or the two-LXC architecture.
- Treating moving container tags or apt repository state as equivalent to Renovate-reviewed source updates.
