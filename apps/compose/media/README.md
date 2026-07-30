# Media Compose Project

Gluetun owns the network namespace used by qBittorrent. Proton WireGuard port
forwarding is enabled natively and Gluetun updates qBittorrent through its
loopback Web API whenever the forwarded port changes; no custom WireGuard,
server-selection, NAT-PMP, or killswitch script remains.

The pinned official LinuxServer VueTorrent mod provides assets at
`/vuetorrent/public`, and Ansible selects that public directory as
qBittorrent's alternative Web UI.

Bind mounts are used for data that is shared, backed up, migrated, or inspected
outside a single container:

- `/srv/homelab/downloads` is read-write in qBittorrent.
- `/srv/homelab/copyparty/public` is read-write in both qBittorrent and
  Copyparty so public torrents can continue seeding.
- completed downloads and the shared-readonly tree are read-only in Copyparty.
- qBittorrent and Copyparty configuration/state are bind mounts so native-LXC
  state can be migrated and backed up.

Opaque Gluetun runtime state uses a named volume.

MeTube is available only on the private network at
`https://metube.home.hchu.me`. It saves browser-requested downloads under
`/srv/homelab/copyparty/downloads`; completed files remain there until the user
presses MeTube's trash button, which also deletes the server-side file.
