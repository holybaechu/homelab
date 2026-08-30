# Base identities live with the Dockerfile. Renovate updates these reviewed
# digest-pinned lines; the workflow has no image-variable control plane.
FROM ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 AS uv
FROM docker.io/kalilinux/kali-rolling:latest@sha256:cf2a9424328d67e1dbda1623a4691e6faff6444dac7506fd0c1b7e26c0d3ab86

COPY --from=uv /uv /uvx /usr/local/bin/
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1

# Package resolution and Python assembly happen once in CI.  Production only
# pulls the resulting manifest digest; it never executes this Dockerfile.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
      binutils binwalk ca-certificates checksec chromium chromium-driver curl \
      dnsutils ffuf file gdb gdb-multiarch gdb-peda git gobuster hashcat jq \
      john libasound2t64 libgtk-3-0 libimage-exiftool-perl libx11-xcb1 ltrace \
      netcat-openbsd nmap openssl python3 python3-pip python3-pwntools \
      python3-venv socat sqlmap strace tshark unzip wget whois yara xvfb zip \
    && rm -rf /var/lib/apt/lists/* \
    && uv pip install --system --break-system-packages --no-cache 'camoufox[geoip]==0.5.4' \
    && (getent group 1000 >/dev/null || groupadd --gid 1000 ctf) \
    && if getent passwd 1000 >/dev/null; then \
         ctf_user="$(getent passwd 1000 | cut -d: -f1)"; \
         usermod --home /home/ctf --shell /bin/bash "${ctf_user}"; \
       else \
         useradd --uid 1000 --gid 1000 --create-home --home-dir /home/ctf \
           --shell /bin/bash ctf; \
       fi \
    && install -d -o 1000 -g 1000 -m 0700 /home/ctf \
    && install -d -o 1000 -g 1000 -m 0700 /workspace \
    && command -v uv >/dev/null \
    && command -v chromium >/dev/null \
    && command -v python3 >/dev/null \
    && test "$(getent passwd 1000 | cut -d: -f6)" = /home/ctf \
    && ! command -v docker >/dev/null

LABEL org.opencontainers.image.title="OpenClaw immutable CTF sandbox" \
      org.opencontainers.image.description="Prebuilt analysis image without Docker tooling or a host socket"
ENV HOME=/home/ctf PATH=/home/ctf/.local/bin:${PATH}
USER 1000:1000
WORKDIR /workspace
CMD ["/bin/bash"]
