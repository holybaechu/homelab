# Hermes Compose Project

Runs the official Hermes Agent image as a supervised Discord gateway. A small
derived layer adds the 1Password CLI required by the existing Newrrow skill.
The upstream image already includes Chromium, browser automation, SSH, git,
Docker CLI, ffmpeg, and s6 supervision.

Hermes state and workspace stay as bind mounts because they contain user-owned
sessions, skills, memory, Git configuration, and working files that must remain
accessible for backup and migration. No Docker socket is mounted into Hermes.

The old public `hermes-config-webhook` and native systemd source build are not
part of this clean migration. The persisted `/opt/data` tree remains the source
of truth and is upgraded in place by the official image.
