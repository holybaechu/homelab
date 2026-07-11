FROM nousresearch/hermes-agent:v2026.7.7.2@sha256:9c841866021c54c4596849f6135717e8a4d52ba510b7f52c50aef1de1a283973

USER root

RUN install -d -m 0755 /usr/share/keyrings \
    && curl -fsSL https://downloads.1password.com/linux/keys/1password.asc \
      -o /usr/share/keyrings/1password-archive-keyring.asc \
    && printf '%s\n' \
      'deb [arch=amd64 signed-by=/usr/share/keyrings/1password-archive-keyring.asc] https://downloads.1password.com/linux/debian/amd64 stable main' \
      > /etc/apt/sources.list.d/1password.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends 1password-cli \
    && rm -rf /var/lib/apt/lists/*

# Do not add USER here. The upstream s6 entrypoint starts as root, reconciles
# bind-mount ownership, and then drops to HERMES_UID:HERMES_GID.
