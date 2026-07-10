FROM nousresearch/hermes-agent:v0.18.2

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
