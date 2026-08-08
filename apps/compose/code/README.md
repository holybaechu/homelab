# T3 Code

T3 Code runs as a private headless server at
`https://code.home.hchu.me`. Traefik accepts the route only from the homelab
LAN and Tailnet ranges.

The image uses the official minimal `kalilinux/kali-rolling` image as its
final base and adds Node.js 24, T3 Code, and common development tools. It does
not mount the Docker socket or receive host capabilities.

Persistent paths:

- `/srv/homelab/docker-apps/t3code/home` stores T3 state, pairing sessions,
  provider configuration, and provider credentials.
- `/srv/homelab/workspaces` stores projects exposed to coding agents.

T3 Code does not bundle provider CLIs or credentials. Install and authenticate
the required provider inside the persistent environment before starting its
first session. Treat pairing links and provider credentials as secrets.
