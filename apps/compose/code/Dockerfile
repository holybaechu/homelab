FROM node:24.19.0-bookworm-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03 AS node-runtime

FROM kalilinux/kali-rolling:latest@sha256:d2560fdc054793be92236322113fa231e609f95403dd68c613ca864b0d872b1f

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
