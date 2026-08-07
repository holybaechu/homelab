# Media Compose Project

The `qbittorrent` service is the single active qBittorrent instance. It uses
the host's direct network path, retains its state under
`/srv/homelab/docker-apps/qbittorrent`, and is available through the private
Traefik route at `https://qbt.home.hchu.me`. It has no Gluetun, Proton,
WireGuard, or `tun0` dependency.

The instance publishes peer port `35435` over both TCP and UDP on the
Docker-apps host. For direct inbound connectivity, forward WAN TCP and UDP
`35435` to `192.168.0.3:35435`. Its Web UI port is not published and remains
reachable only through the private Traefik route.

The pinned official LinuxServer VueTorrent mod provides assets at
`/vuetorrent/public`, and Ansible selects that directory for the qBittorrent
Web UI.

The active service uses these bind mounts:

- `/srv/homelab/downloads` is read-write for qBittorrent.
- `/srv/homelab/copyparty/public` is read-write for qBittorrent and Copyparty
  so selected files can continue seeding.
- `/srv/homelab/docker-apps/qbittorrent` contains the active qBittorrent
  configuration and state.
- completed downloads and the shared-readonly tree are read-only in Copyparty.

The retired VPN client's former state at
`/srv/homelab/docker-apps/qbittorrent-vpn` is preserved but unmanaged for
recovery. It is not mounted by an active service, and Ansible does not modify
its application contents or delete it.

MeTube is available only on the private network at
`https://metube.home.hchu.me`. It saves browser-requested downloads under
`/srv/homelab/copyparty/downloads`; completed files remain there until the user
presses MeTube's trash button, which also deletes the server-side file.
