FROM kalilinux/kali-rolling

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /usr/local/bin/

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        binutils \
        binwalk \
        ca-certificates \
        checksec \
        curl \
        dnsutils \
        file \
        gdb \
        gdb-multiarch \
        gdb-peda \
        git \
        jq \
        libimage-exiftool-perl \
        ltrace \
        netcat-openbsd \
        nmap \
        openssl \
        python3 \
        python3-pip \
        python3-pwntools \
        python3-venv \
        socat \
        strace \
        tshark \
        unzip \
        wget \
        whois \
        zip \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1001 ctf \
    && useradd --uid 1001 --gid 1001 --create-home --home-dir /home/ctf \
      --shell /bin/bash ctf

LABEL org.opencontainers.image.title="OpenClaw isolated CTF Kali sandbox" \
      org.opencontainers.image.description="CTF analysis image with uv and without Docker tooling or host socket mounts"

ENV HOME=/home/ctf \
    PATH=/home/ctf/.local/bin:${PATH}

USER 1001:1001
WORKDIR /workspace
CMD ["/bin/bash"]
