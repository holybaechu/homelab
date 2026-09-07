# Base identities live with the Dockerfile. Renovate updates these reviewed
# digest-pinned lines; the workflow has no image-variable control plane.
FROM ghcr.io/openclaw/openclaw:2026.7.1-2@sha256:8789721d2e9b24b780a1504b56deb4c6bd5c7dbf96a1dd117e7c45c2ed72c8ac AS plugin-assembly
USER root
RUN mkdir -p /opt/openclaw-plugins/project \
    && npm install --omit=dev --ignore-scripts --no-audit --no-fund \
      --prefix /opt/openclaw-plugins/project \
      @openclaw/codex@2026.7.1-1 \
      @openclaw/discord@2026.7.1 \
      @openclaw/exa-plugin@2026.7.1

FROM docker.io/library/python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS patch-assembly
COPY --from=plugin-assembly /opt/openclaw-plugins /opt/openclaw-plugins
COPY patches/ /patches/
RUN python /patches/patch-openclaw-codex-sandbox-cwd.py \
      /opt/openclaw-plugins/project/node_modules/@openclaw/codex \
    && python /patches/patch-openclaw-discord-autothread-queue.py \
      /opt/openclaw-plugins

FROM docker.io/library/docker:29-cli@sha256:000bb62ff495f986c9f5578eb67cc2cb98b91138eda81d7762d5371eb8a497fe AS docker-cli

FROM ghcr.io/openclaw/openclaw:2026.7.1-2@sha256:8789721d2e9b24b780a1504b56deb4c6bd5c7dbf96a1dd117e7c45c2ed72c8ac
USER root
COPY --from=patch-assembly --chown=0:0 /opt/openclaw-plugins /opt/openclaw-plugins
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
RUN test -x /usr/local/bin/docker \
    && docker --version \
    && node --version \
    && test -f /opt/openclaw-plugins/project/node_modules/@openclaw/codex/package.json \
    && test -f /opt/openclaw-plugins/project/node_modules/@openclaw/discord/package.json \
    && test -f /opt/openclaw-plugins/project/node_modules/@openclaw/exa-plugin/package.json
ENV OPENCLAW_PLUGIN_ROOT=/opt/openclaw-plugins/project/node_modules \
    OPENCLAW_NO_AUTO_UPDATE=1 \
    OPENCLAW_SERVICE_REPAIR_POLICY=external \
    OPENCLAW_SUPERVISOR_MODE=external
LABEL org.opencontainers.image.title="Homelab OpenClaw Gateway" \
      org.opencontainers.image.description="Preassembled OpenClaw gateway, plugins, patches, and Docker client"
USER 1000:1000
