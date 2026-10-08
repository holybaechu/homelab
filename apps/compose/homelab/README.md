# Homelab Compose package

Application configuration for `docker_apps`, deployed as the `homelab` project
in `/opt/homelab/compose`. Docker stays in the existing unprivileged LXC.

Edit [compose.yml](compose.yml) or [config/](config/), then use
[local validation](../../../README.md#validate-locally) and
[deployment operations](../../../docs/operations.md#deploy-apps). Images and
GitHub Actions remain pinned. Deployment is manual; changed services update with
native Compose, and mounted configuration changes use targeted recreation.

[prepare_release.py](prepare_release.py) prepares private configuration from the
version-2 apps bundle and topology. [deploy.py](deploy.py) installs stable
directory mounts, saves previous configuration, backs up identity data before
upgrades, and invokes Compose. [smoke.sh](smoke.sh) reads DNS and access behavior
without changing services. Keep secrets and generated files outside the repo.

Authentik manages browser access to MeTube and Copyparty, and native OIDC for
Headscale and Proxmox. Copyparty `/public` stays readable without login. Private
native clients use the WebDAV endpoint. Both Tailscale and Headscale remain;
management stays on hosted Tailscale until an explicit cutover. See
[identity operations](../../../docs/operations.md#copyparty-and-proxmox-identity).

Edit managed settings here. AdGuard's normalized runtime file is preserved until
its template, credentials, or topology changes. Copyparty configuration is
read-only, and qBittorrent settings are reapplied on its next restart.

Named volumes and durable data mounts are declared in Compose. Back them up
independently using [recovery](../../../docs/recovery.md). Configuration recovery
does not undo database migrations or restore user files.
