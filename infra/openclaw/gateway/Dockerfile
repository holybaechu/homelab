# Every FROM value is supplied as an exact repository@sha256 digest by the
# release-contract generator.  No tag is accepted by the promotion path.
ARG OPENCLAW_BASE_REF
ARG PYTHON_BASE_REF
ARG DOCKER_CLI_REF

FROM ${OPENCLAW_BASE_REF} AS plugin-assembly
USER root
RUN mkdir -p /opt/openclaw-plugins/project \
    && npm install --omit=dev --ignore-scripts --no-audit --no-fund \
      --prefix /opt/openclaw-plugins/project \
      @openclaw/codex@2026.7.1-1 \
      @openclaw/discord@2026.7.1 \
      @openclaw/exa-plugin@2026.7.1

FROM ${PYTHON_BASE_REF} AS patch-assembly
COPY --from=plugin-assembly /opt/openclaw-plugins /opt/openclaw-plugins
COPY patches/ /patches/
RUN python /patches/patch-openclaw-codex-sandbox-cwd.py \
      /opt/openclaw-plugins/project/node_modules/@openclaw/codex \
    && python /patches/patch-openclaw-discord-autothread-queue.py \
      /opt/openclaw-plugins

FROM ${DOCKER_CLI_REF} AS docker-cli

FROM ${OPENCLAW_BASE_REF}
USER root
COPY --from=patch-assembly --chown=0:0 /opt/openclaw-plugins /opt/openclaw-plugins
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
ENV OPENCLAW_PLUGIN_ROOT=/opt/openclaw-plugins/project/node_modules \
    OPENCLAW_NO_AUTO_UPDATE=1 \
    OPENCLAW_SERVICE_REPAIR_POLICY=external \
    OPENCLAW_SUPERVISOR_MODE=external
LABEL org.opencontainers.image.title="Homelab OpenClaw Gateway" \
      org.opencontainers.image.description="Preassembled OpenClaw gateway, plugins, patches, and Docker client"
USER 1000:1000
