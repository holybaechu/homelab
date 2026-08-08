FROM node:24.18.0-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d AS node-runtime

FROM kalilinux/kali-rolling:latest@sha256:7a8e05db54fbfa3891c35e11d6cdeac7d6f42efddb53c509b2a7b9c2764cad73

ARG T3_VERSION=0.0.28

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        curl \
        git \
        jq \
        openssh-client \
        procps \
        python3 \
        python3-pip \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node-runtime /usr/local/ /usr/local/

RUN groupadd --gid 1000 t3code \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash t3code \
    && npm install --global "t3@${T3_VERSION}" \
    && npm cache clean --force \
    && node --version \
    && t3 --help >/dev/null

ENV HOME=/home/t3code

USER 1000:1000
WORKDIR /workspace

EXPOSE 3773

CMD ["t3", "serve", "--host", "0.0.0.0"]
