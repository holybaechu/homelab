#!/bin/sh
set -eu

install -d -o 1000 -g 1000 -m 0700 /config/qBittorrent
install -o 1000 -g 1000 -m 0600 \
  /run/homelab/qBittorrent.conf \
  /config/qBittorrent/qBittorrent.conf

exec /init
