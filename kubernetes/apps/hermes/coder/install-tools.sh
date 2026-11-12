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
curl --fail --silent --show-error --location --max-time 60 --retry 2 \
  https://github.com/trycua/cua/releases/download/cua-sdk-v0.16.0/cua-cli-0.16.0-linux-x64.tar.gz \
  -o cua.tar.gz
curl --fail --silent --show-error --location --max-time 60 --retry 2 \
  https://github.com/twpayne/chezmoi/releases/download/v2.74.1/chezmoi_2.74.1_linux_amd64.tar.gz \
  -o chezmoi.tar.gz
sha256sum -c /bootstrap/coder-tools.sha256
tar -xJf mise.tar.xz --strip-components=2 -C /opt/coder/bin mise/bin/mise
tar -xzf gh.tar.gz --strip-components=2 -C /opt/coder/bin gh_2.103.2_linux_amd64/bin/gh
tar -xzf cua.tar.gz -C /opt/coder/bin cua
tar -xzf chezmoi.tar.gz -C /opt/coder/bin chezmoi
mv /opt/coder/bin/gh /opt/coder/bin/gh-real
cp /bootstrap/coder-gh.sh /opt/coder/bin/gh
cp /bootstrap/coder-gitconfig /opt/coder/gitconfig
install -m 0555 /bootstrap/profile-sync.sh /opt/coder/bin/hermes-profile-sync
chmod 755 /opt/coder/bin/mise /opt/coder/bin/gh /opt/coder/bin/gh-real /opt/coder/bin/cua
/opt/coder/bin/mise --version
/opt/coder/bin/gh-real --version
/opt/coder/bin/cua --version
/opt/coder/bin/chezmoi --version

export PATH=/opt/coder/bin:$PATH
kubectl_version=$(printf '%s' "$KUBERNETES_VERSION" | sed 's/^v//')
talos_version=$(printf '%s' "$TALOS_VERSION" | sed 's/^v//')
flux_version=$(printf '%s' "$FLUX_VERSION" | sed 's/^v//')
mise install "kubectl@$kubectl_version" "flux2@$flux_version" "talosctl@$talos_version"
# Direct links make the administration tools available outside trusted checkouts.
ln -sf "$(mise where "kubectl@$kubectl_version")/kubectl" /opt/coder/bin/kubectl
ln -sf "$(mise where "flux2@$flux_version")/flux" /opt/coder/bin/flux
ln -sf "$(mise where "talosctl@$talos_version")/talosctl" /opt/coder/bin/talosctl
