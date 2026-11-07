#!/bin/sh
set -eu

# The image defaults HOME to /root even when this init container runs as UID 10000.
export HOME=/tmp
cd /tmp
curl --fail --silent --show-error --location --max-time 60 --retry 2 \
  https://github.com/jdx/mise/releases/download/v2026.11.3/mise-v2026.11.3-linux-x64.tar.xz \
  -o mise.tar.xz
curl --fail --silent --show-error --location --max-time 60 --retry 2 \
  https://github.com/cli/cli/releases/download/v2.103.2/gh_2.103.2_linux_amd64.tar.gz \
  -o gh.tar.gz
sha256sum -c /bootstrap/coder-tools.sha256
tar -xJf mise.tar.xz --strip-components=2 -C /opt/coder/bin mise/bin/mise
tar -xzf gh.tar.gz --strip-components=2 -C /opt/coder/bin gh_2.103.2_linux_amd64/bin/gh
mv /opt/coder/bin/gh /opt/coder/bin/gh-real
cp /bootstrap/coder-gh.sh /opt/coder/bin/gh
cp /bootstrap/coder-gitconfig /opt/coder/gitconfig
chmod 755 /opt/coder/bin/mise /opt/coder/bin/gh /opt/coder/bin/gh-real
/opt/coder/bin/mise --version
/opt/coder/bin/gh-real --version
