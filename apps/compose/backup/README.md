# Backup Compose Project

Runs Restic immediately at startup and then every 24 hours. Backups are
encrypted and sent to an off-host S3-compatible repository. The source mounts
are read-only and include:

- qBittorrent configuration and torrent metadata,
- the complete downloads tree,
- Copyparty public/shared data,
- Copyparty's migrated runtime/index state.

Retention keeps 7 daily, 5 weekly, and 12 monthly snapshots. Restore into a
staging directory first; never restore over running containers.

Configure `BACKUP_RESTIC_REPOSITORY`, `BACKUP_RESTIC_PASSWORD`,
`BACKUP_AWS_ACCESS_KEY_ID`, and `BACKUP_AWS_SECRET_ACCESS_KEY` in the GitHub
`prod` environment. The repository may be Cloudflare R2, Backblaze B2 S3,
MinIO, or another Restic-compatible S3 endpoint.
