FROM kalilinux/kali-rolling

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

RUN groupadd --gid 1000 ctf \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/ctf \
      --shell /bin/bash ctf

LABEL org.opencontainers.image.title="OpenClaw isolated CTF Kali sandbox" \
      org.opencontainers.image.description="Non-root CTF analysis image without Docker tooling or host socket mounts"

ENV HOME=/home/ctf \
    PATH=/home/ctf/.local/bin:${PATH}

USER 1000:1000
WORKDIR /workspace
CMD ["/bin/bash"]
