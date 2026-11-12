#!/bin/sh
set -eu
umask 077
# Run in the empty runtime volume; keep the complete released distribution.
curl --fail --silent --show-error --location --max-time 120 --retry 2 \
  https://github.com/pingdotgg/t3code/releases/download/v0.1.9/t3-0.1.9-linux-x64.tar.gz \
  --output t3.tar.gz
printf '%s  %s\n' \
  0141025335da334759cecd8db3483e09772f3600ca03674698524e7d94291362 \
  t3.tar.gz | sha256sum --check --strict
tar -xzf t3.tar.gz --strip-components=1
rm t3.tar.gz
./t3 --version | grep -Fx 't3 v0.1.9'
mkdir bin
curl --fail --silent --show-error --location --max-time 60 --retry 2 \
  https://github.com/cli/cli/releases/download/v2.103.2/gh_2.103.2_linux_amd64.tar.gz \
  --output gh.tar.gz
# Reuse only gh's entry from the shared publisher-verified tool checksums.
grep '  gh.tar.gz$' /bootstrap/coder-tools.sha256 | sha256sum --check --strict
tar -xzf gh.tar.gz --strip-components=2 -C bin gh_2.103.2_linux_amd64/bin/gh
rm gh.tar.gz
mv bin/gh bin/gh-real
cp /bootstrap/coder-gh.sh bin/gh
cp /bootstrap/coder-gitconfig gitconfig
chmod 755 bin/gh bin/gh-real
./bin/gh-real --version
