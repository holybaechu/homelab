# Media Compose Project

Two qBittorrent instances keep private-tracker and public-tracker activity in
separate clients:

- `qbittorrent` is the existing, direct instance. It retains the existing
  `/srv/homelab/docker-apps/qbittorrent` state and is available at
  `https://public.qbt.home.hchu.me`.
- `qbittorrent-vpn` is a new instance with independent state under
  `/srv/homelab/docker-apps/qbittorrent-vpn`. It shares Gluetun's network
  namespace and is available at `https://qbt.home.hchu.me`.

The direct instance publishes its configured peer port, `35435` by default,
over both TCP and UDP on the Docker-apps host. For direct inbound connectivity,
forward that same WAN port to the Docker-apps LXC. Its Web UI port is not
published and remains reachable only through the private Traefik route.

Gluetun enables Proton WireGuard port forwarding and updates only
`qbittorrent-vpn` through the shared namespace's loopback Web API whenever the
forwarded port changes. The VPN client remains bound to `tun0`; the direct
client has no VPN-interface binding.

The pinned official LinuxServer VueTorrent mod provides assets at
`/vuetorrent/public`, and Ansible selects that public directory for both
qBittorrent Web UIs.

Both clients mount the same download trees so files can be reassigned without
copying their payloads, but their torrent databases and settings are isolated:

- `/srv/homelab/downloads` is read-write in both qBittorrent instances.
- `/srv/homelab/copyparty/public` is read-write in both qBittorrent instances
  and Copyparty so selected files can continue seeding.
- completed downloads and the shared-readonly tree are read-only in Copyparty.
- each qBittorrent configuration/state directory and Copyparty state are bind
  mounts so native-LXC state can be migrated and backed up.

The first deployment does not classify or migrate existing torrents. Existing
torrents remain in `qbittorrent` and therefore begin using the direct public IP
after cutover. Pause or move any torrents that must remain VPN-routed before
deploying this change. The new VPN instance starts with an empty torrent list.

Opaque Gluetun runtime state uses a named volume.

MeTube is available only on the private network at
`https://metube.home.hchu.me`. It saves browser-requested downloads under
`/srv/homelab/copyparty/downloads`; completed files remain there until the user
presses MeTube's trash button, which also deletes the server-side file.
