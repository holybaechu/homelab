# Media Compose Project

Runs qBittorrent behind gluetun and Copyparty on the same Docker host so
shared storage can use direct bind-mount modes instead of cross-LXC ACLs.

Storage policy:

- qBittorrent mounts `/downloads` read-write.
- qBittorrent mounts `/public` read-write so public content can keep seeding.
- Copyparty mounts `/srv/downloads` read-only from `/downloads/complete`.
- Copyparty mounts `/srv/shared-readonly` read-only.
- Copyparty mounts `/srv/public` read-write for the existing admin/upload model.

Copyparty user data keeps plaintext `password` entries in `COPYPARTY_USERS_JSON`.
Do not change to hashed-password entries unless explicitly requested.
